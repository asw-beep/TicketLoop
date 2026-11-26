"""The client for fakepay, and the settings it is built from. What matters is which answers count
as "no" and which as "no answer": only a "no" may release a ticket."""

import json
from collections.abc import Callable
from typing import Any

import httpx2 as httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.infrastructure.payments import FakepayGateway, GatewayRefused, GatewayUnavailable

INTENT = {
    "transaction_id": "txn_1",
    "idempotency_key": "k",
    "amount_paise": 250_000,
    "currency": "INR",
    "status": "open",
    "checkout_url": "http://localhost:8090/checkout/txn_1",
    "expires_at": 1.0,
}


def gateway(answer: Callable[[httpx.Request], httpx.Response]) -> FakepayGateway:
    return FakepayGateway(
        httpx.Client(base_url="http://fakepay", transport=httpx.MockTransport(answer))
    )


def create(client: FakepayGateway) -> Any:
    return client.create_intent(
        idempotency_key="k", amount_paise=250_000, currency="INR", return_url="http://localhost/o"
    )


def test_creating_an_intent_sends_the_payments_own_key() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=INTENT)

    intent = create(gateway(answer))

    assert (intent.transaction_id, intent.status) == ("txn_1", "open")
    assert intent.snapshot == INTENT
    (request,) = seen
    assert request.headers["idempotency-key"] == "k"
    assert json.loads(request.content)["amount_paise"] == 250_000


@pytest.mark.parametrize("status", [500, 503, 408, 425, 429])
def test_an_answer_that_may_change_is_no_answer(status: int) -> None:
    client = gateway(lambda _: httpx.Response(status))
    with pytest.raises(GatewayUnavailable):
        create(client)
    with pytest.raises(GatewayUnavailable):
        client.by_transaction("txn_1")


@pytest.mark.parametrize("status", [400, 409, 422])
def test_a_no_to_creating_is_a_refusal(status: int) -> None:
    with pytest.raises(GatewayRefused):
        create(gateway(lambda _: httpx.Response(status)))


@pytest.mark.parametrize("status", [401, 403, 404, 405])
def test_a_no_to_how_we_asked_says_nothing_about_the_payment(status: int) -> None:
    # A wrong key or a wrong address. Calling that a refusal would put the ticket back on sale.
    with pytest.raises(GatewayUnavailable):
        create(gateway(lambda _: httpx.Response(status)))


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout])
def test_silence_is_no_answer(error: type[httpx.TransportError]) -> None:
    def answer(_: httpx.Request) -> httpx.Response:
        raise error("silence")

    client = gateway(answer)
    for ask in (
        lambda: create(client),
        lambda: client.by_transaction("txn_1"),
        lambda: client.by_idempotency_key("k"),
    ):
        with pytest.raises(GatewayUnavailable):
            ask()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>maintenance</html>"),
        httpx.Response(200, json={"status": "open"}),
        httpx.Response(200, json=INTENT | {"status": "perhaps"}),
        httpx.Response(200, json=[]),
        httpx.Response(302, headers={"location": "http://elsewhere/"}),
    ],
)
def test_an_answer_that_makes_no_sense_is_no_answer(response: httpx.Response) -> None:
    client = gateway(lambda _: response)
    with pytest.raises(GatewayUnavailable):
        create(client)
    with pytest.raises(GatewayUnavailable):
        client.by_transaction("txn_1")


def test_a_lookup_may_find_nothing() -> None:
    client = gateway(lambda _: httpx.Response(404))
    assert client.by_transaction("txn_1") is None
    assert client.by_idempotency_key("k") is None


def test_a_refused_lookup_is_not_the_same_as_finding_nothing() -> None:
    with pytest.raises(GatewayUnavailable):
        gateway(lambda _: httpx.Response(401)).by_idempotency_key("k")


# ---------------------------------------------------------------- settings


def test_production_refuses_the_built_in_webhook_secret() -> None:
    with pytest.raises(ValidationError, match="FAKEPAY_WEBHOOK_SECRET"):
        Settings(
            _env_file=None,
            environment="production",
            jwt_secret_key="s" * 48,
            storage_secret_key="a-storage-secret-from-the-environment",
        )


def test_a_short_webhook_secret_is_refused() -> None:
    with pytest.raises(ValidationError, match="32"):
        Settings(_env_file=None, fakepay_webhook_secret="too-short")
