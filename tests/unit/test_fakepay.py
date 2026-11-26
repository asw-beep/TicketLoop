"""fakepay, the pretend gateway: what it promises the API, and the ways it misbehaves on request."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import LOCAL_FAKEPAY_SECRET
from app.modules.payments.errors import BadWebhookSignatureError
from app.modules.payments.webhooks import SIGNATURE_HEADER, TIMESTAMP_HEADER, parse, verify
from fakepay.config import LOCAL_WEBHOOK_SECRET, FakepaySettings
from fakepay.main import create_app
from fakepay.page import rupees
from fakepay.webhooks import WebhookSender

RETURN_URL = "http://localhost:5173/orders/1"


class Gateway:
    """fakepay with its clock and its network in the test's hands."""

    def __init__(self, **settings: Any) -> None:
        self.now = 1_800_000_000.0
        self.sent: list[tuple[bytes, dict[str, str]]] = []
        self.waits: list[float] = []
        self.answers: list[int] = []  # what the API answers, then 200s
        config = FakepaySettings(**settings)
        sender = WebhookSender(config, post=self._post, later=self._later)
        self.client = TestClient(create_app(config, sender, clock=lambda: self.now))

    def _post(self, url: str, body: bytes, headers: dict[str, str]) -> int:
        self.sent.append((body, headers))
        answer = self.answers.pop(0) if self.answers else 200
        if answer == 0:
            raise httpx.ConnectError("refused")
        return answer

    def _later(self, seconds: float, task: Callable[[], None]) -> None:
        self.waits.append(seconds)
        task()

    def create(self, key: str = "key-1", **fields: Any) -> httpx.Response:
        body = {"amount_paise": 250_000, "currency": "INR", "return_url": RETURN_URL} | fields
        return self.client.post("/v1/intents", json=body, headers={"Idempotency-Key": key})

    def intent(self) -> str:
        return self.create().json()["transaction_id"]

    def choose(self, transaction_id: str, outcome: str) -> httpx.Response:
        return self.client.post(f"/v1/intents/{transaction_id}/outcome", json={"outcome": outcome})


@pytest.fixture
def gateway() -> Gateway:
    return Gateway()


def test_both_sides_have_the_same_built_in_secret() -> None:
    assert LOCAL_WEBHOOK_SECRET == LOCAL_FAKEPAY_SECRET


# ---------------------------------------------------------------- intents


def test_an_intent_is_open_and_says_where_to_pay(gateway: Gateway) -> None:
    response = gateway.create()

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "open"
    assert body["checkout_url"] == f"http://localhost:8090/checkout/{body['transaction_id']}"
    assert body["expires_at"] == gateway.now + 15 * 60


def test_the_same_key_is_the_same_intent(gateway: Gateway) -> None:
    first = gateway.create("k")
    again = gateway.create("k")

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json()
    assert gateway.create("other").json()["transaction_id"] != first.json()["transaction_id"]


def test_the_same_key_for_another_amount_is_refused(gateway: Gateway) -> None:
    gateway.create("k")
    assert gateway.create("k", amount_paise=1).status_code == 409


@pytest.mark.parametrize(
    "fields", [{"amount_paise": 0}, {"currency": "USD"}, {"return_url": "javascript:alert(1)"}]
)
def test_nonsense_is_refused(gateway: Gateway, fields: dict[str, Any]) -> None:
    assert gateway.create(**fields).status_code == 422


def test_an_intent_is_found_by_its_id_and_by_its_key(gateway: Gateway) -> None:
    made = gateway.create("k").json()

    assert gateway.client.get(f"/v1/intents/{made['transaction_id']}").json() == made
    assert gateway.client.get("/v1/intents", params={"idempotency_key": "k"}).json() == made
    assert gateway.client.get("/v1/intents/txn_nope").status_code == 404
    assert gateway.client.get("/v1/intents?idempotency_key=nope").status_code == 404


def test_an_intent_expires_after_fifteen_minutes_and_then_takes_no_money(gateway: Gateway) -> None:
    transaction_id = gateway.intent()
    gateway.now += 15 * 60

    assert gateway.client.get(f"/v1/intents/{transaction_id}").json()["status"] == "expired"
    assert gateway.choose(transaction_id, "succeed").status_code == 409
    assert gateway.sent == []


def test_an_intent_is_settled_once(gateway: Gateway) -> None:
    transaction_id = gateway.intent()

    assert gateway.choose(transaction_id, "succeed").json()["status"] == "succeeded"
    assert gateway.choose(transaction_id, "fail").status_code == 409
    assert gateway.client.get(f"/v1/intents/{transaction_id}").json()["status"] == "succeeded"
    assert len(gateway.sent) == 1


def test_a_settled_intent_does_not_expire(gateway: Gateway) -> None:
    transaction_id = gateway.intent()
    gateway.choose(transaction_id, "succeed")
    gateway.now += 24 * 3600
    assert gateway.client.get(f"/v1/intents/{transaction_id}").json()["status"] == "succeeded"


def test_a_restart_forgets_nothing(tmp_path: Path) -> None:
    db = str(tmp_path / "fakepay.db")
    made = Gateway(db=db).create("k").json()
    after = Gateway(db=db).client.get("/v1/intents", params={"idempotency_key": "k"}).json()
    assert after == made


# ---------------------------------------------------------------- webhooks


def check(sent: tuple[bytes, dict[str, str]]) -> None:
    """The API's own check, run on what fakepay sent."""
    body, headers = sent
    verify(
        LOCAL_FAKEPAY_SECRET,
        body,
        headers[SIGNATURE_HEADER],
        headers[TIMESTAMP_HEADER],
        datetime.now(UTC),
    )


@pytest.mark.parametrize(
    "outcome, type_", [("succeed", "payment.succeeded"), ("fail", "payment.failed")]
)
def test_the_api_accepts_the_webhook(gateway: Gateway, outcome: str, type_: str) -> None:
    transaction_id = gateway.intent()

    gateway.choose(transaction_id, outcome)

    (sent,) = gateway.sent
    check(sent)
    event = parse(sent[0])
    assert (event.type, event.transaction_id) == (type_, transaction_id)
    assert (event.amount_paise, event.currency) == (250_000, "INR")


def test_twice_is_the_same_event_delivered_two_times(gateway: Gateway) -> None:
    gateway.choose(gateway.intent(), "twice")

    first, second = gateway.sent
    check(first)
    check(second)
    assert first[0] == second[0]


def test_a_forged_webhook_fails_the_check_and_is_not_retried(gateway: Gateway) -> None:
    gateway.answers = [401]
    transaction_id = gateway.intent()

    gateway.choose(transaction_id, "bad_signature")

    (sent,) = gateway.sent
    with pytest.raises(BadWebhookSignatureError):
        check(sent)
    # The payment itself did succeed: only reconciliation can find that out.
    assert gateway.client.get(f"/v1/intents/{transaction_id}").json()["status"] == "succeeded"


def test_no_webhook_is_none_at_all(gateway: Gateway) -> None:
    transaction_id = gateway.intent()
    assert gateway.choose(transaction_id, "no_webhook").json()["status"] == "succeeded"
    assert gateway.sent == []


def test_a_late_webhook_waits(gateway: Gateway) -> None:
    gateway.choose(gateway.intent(), "delay")
    assert gateway.waits == [10.0]
    assert len(gateway.sent) == 1


@pytest.mark.parametrize("answer", [500, 401, 0])
def test_an_undelivered_webhook_is_tried_again(gateway: Gateway, answer: int) -> None:
    gateway.answers = [answer, answer]

    gateway.choose(gateway.intent(), "succeed")

    assert len(gateway.sent) == 3
    assert len({body for body, _ in gateway.sent}) == 1  # one event, so it applies once
    assert gateway.waits == [0.0, 1.0, 3.0]


def test_it_gives_up_after_three_more_tries(gateway: Gateway) -> None:
    gateway.answers = [500] * 10
    gateway.choose(gateway.intent(), "succeed")
    assert len(gateway.sent) == 4


# ---------------------------------------------------------------- the checkout page


def test_the_checkout_page_shows_the_amount_and_every_outcome(gateway: Gateway) -> None:
    page = gateway.client.get(f"/checkout/{gateway.intent()}").text

    assert "₹2,500" in page
    for outcome in ("succeed", "fail", "delay", "twice", "bad_signature", "no_webhook"):
        assert f'value="{outcome}"' in page


def test_paying_goes_back_to_the_order(gateway: Gateway) -> None:
    transaction_id = gateway.intent()

    response = gateway.client.post(
        f"/checkout/{transaction_id}", data={"outcome": "succeed"}, follow_redirects=False
    )

    assert (response.status_code, response.headers["location"]) == (303, RETURN_URL)
    assert json.loads(gateway.sent[0][0])["type"] == "payment.succeeded"


def test_paying_twice_shows_that_it_was_paid(gateway: Gateway) -> None:
    transaction_id = gateway.intent()
    pay = {"url": f"/checkout/{transaction_id}", "data": {"outcome": "succeed"}}
    gateway.client.post(**pay, follow_redirects=False)

    again = gateway.client.post(**pay)  # Back, then Pay again

    assert "This payment went through." in again.text
    assert "<form" not in again.text
    assert f'href="{RETURN_URL}"' in again.text
    assert len(gateway.sent) == 1


def test_an_expired_checkout_page_takes_no_money(gateway: Gateway) -> None:
    transaction_id = gateway.intent()
    gateway.now += 15 * 60
    page = gateway.client.get(f"/checkout/{transaction_id}").text
    assert "has expired" in page and "<form" not in page


def test_an_address_cannot_put_script_in_the_page(gateway: Gateway) -> None:
    made = gateway.create(return_url='http://localhost/"><script>alert(1)</script>').json()
    gateway.choose(made["transaction_id"], "succeed")
    assert "<script>" not in gateway.client.get(f"/checkout/{made['transaction_id']}").text


def test_an_unknown_checkout_is_404(gateway: Gateway) -> None:
    assert gateway.client.get("/checkout/txn_nope").status_code == 404
    assert gateway.client.post("/checkout/txn_nope", data={"outcome": "succeed"}).status_code == 404


@pytest.mark.parametrize(
    "paise, shown",
    [
        (100, "1"),
        (99_900, "999"),
        (250_000, "2,500"),
        (250_050, "2,500.50"),
        (12_345_678_00, "1,23,45,678"),
    ],
)
def test_rupees_are_grouped_the_indian_way(paise: int, shown: str) -> None:
    assert rupees(paise) == shown
