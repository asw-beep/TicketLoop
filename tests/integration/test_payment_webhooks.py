"""Webhooks: only the gateway's count, each counts once, and none can do what it should not."""

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.payments import webhooks
from app.modules.payments.models import Payment, PaymentEvent
from app.modules.payments.service import fail
from app.modules.reservations.models import Reservation
from tests.integration.api import problem_code
from tests.integration.conftest import new_account
from tests.integration.payments import (
    WEBHOOK_PATH,
    Fakepay,
    event,
    fakepay,  # noqa: F401
    paying,
    post_intent,
    post_order,
    post_webhook,
    reserve,
)
from tests.integration.test_orders import audit, set_row
from tests.integration.test_payment_intents import count, statuses

pytestmark = pytest.mark.integration

UNTOUCHED = ("PAYMENT_PENDING", "PAYMENT_PENDING", "RESERVED", "PENDING")
PAID = ("PAID", "CONFIRMED", "RESERVED", "SUCCEEDED")
FAILED = ("PAYMENT_FAILED", "FAILED", "ACTIVE", "FAILED")


def outcomes(db: Session) -> list[str]:
    return list(db.scalars(select(PaymentEvent.processing_status)))


# ---------------------------------------------------------------- what they do


def test_a_success_confirms_the_purchase_and_keeps_the_ticket(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    _, order, intent = paying(api, db_session)

    fakepay.choose(intent["provider_transaction_id"], "succeed")

    assert [r.status_code for r in fakepay.webhooks] == [200]
    assert fakepay.webhooks[0].json() == {"received": True}
    assert statuses(db_session, order) == PAID  # SOLD comes with the transfer, in M9
    for entity, action in (
        (intent["payment_id"], "payment.succeeded"),
        (order["id"], "order.paid"),
        (order["reservation_id"], "reservation.confirmed"),
    ):
        assert audit(db_session, entity)[-1] == (action, None)
    row = db_session.scalar(select(AuditLog).where(AuditLog.action == "order.paid"))
    assert row.details["source"] == "webhook"
    assert outcomes(db_session) == ["PROCESSED"]
    stored = db_session.scalar(select(PaymentEvent))
    assert str(stored.payment_id) == intent["payment_id"]
    assert stored.payload["transaction_id"] == intent["provider_transaction_id"]


def test_a_failure_puts_the_ticket_back_on_sale(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    buyer, order, intent = paying(api, db_session)

    fakepay.choose(intent["provider_transaction_id"], "fail")

    assert statuses(db_session, order) == FAILED
    assert db_session.scalar(select(Payment.failure_reason)) == "declined"
    assert audit(db_session, order["listing_id"])[-1] == ("listing.released", None)
    # The buyer may try again, from the start.
    again = reserve(api, order["listing_id"], buyer)
    second = post_order(api, again["id"], buyer).json()
    assert post_intent(api, second["id"], buyer).status_code == 201


def test_a_failure_after_the_listings_own_end_does_not_put_it_back_on_sale(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    from app.modules.listings.models import Listing

    _, order, intent = paying(api, db_session)
    past = datetime.now(UTC) - timedelta(minutes=1)
    set_row(db_session, Listing, order["listing_id"], expires_at=past)
    fakepay.choose(intent["provider_transaction_id"], "fail")
    assert statuses(db_session, order)[2] == "EXPIRED"


def test_a_payment_made_after_the_ten_minutes_still_counts(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    _, order, intent = paying(api, db_session)
    past = datetime.now(UTC) - timedelta(minutes=4)
    set_row(db_session, Reservation, order["reservation_id"], expires_at=past)
    fakepay.choose(intent["provider_transaction_id"], "succeed")
    assert statuses(db_session, order) == PAID


# ---------------------------------------------------------------- again and again


def test_ten_deliveries_of_one_event_change_things_once(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    _, order, intent = paying(api, db_session)
    body = event(intent)

    replies = [post_webhook(api, body) for _ in range(10)]

    assert {r.status_code for r in replies} == {200}
    assert statuses(db_session, order) == PAID
    assert outcomes(db_session) == ["PROCESSED"]
    assert [a for a, _ in audit(db_session, order["id"])].count("order.paid") == 1


def test_a_second_event_about_a_settled_payment_is_ignored(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    _, order, intent = paying(api, db_session)
    post_webhook(api, event(intent))

    for type_ in ("payment.succeeded", "payment.failed"):
        assert post_webhook(api, event(intent, type=type_)).status_code == 200

    assert statuses(db_session, order) == PAID
    assert sorted(outcomes(db_session)) == ["IGNORED", "IGNORED", "PROCESSED"]


def test_a_success_after_we_gave_up_is_kept_and_shouted_about_but_takes_no_ticket(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, order, intent = paying(api, db_session)
    fail(db_session, uuid.UUID(intent["payment_id"]), reason="timeout", source="expiry")
    db_session.commit()
    reserve(api, order["listing_id"], new_account(api))  # someone else has it by now

    assert post_webhook(api, event(intent)).status_code == 200

    assert statuses(db_session, order) == ("PAYMENT_FAILED", "FAILED", "RESERVED", "FAILED")
    assert outcomes(db_session) == ["IGNORED"]
    assert "payment.succeeded_after_failing" in caplog.text


def test_an_event_of_a_kind_we_do_not_know_is_kept_and_ignored(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    _, order, intent = paying(api, db_session)
    assert post_webhook(api, event(intent, type="payment.refunded")).status_code == 200
    assert statuses(db_session, order) == UNTOUCHED
    assert outcomes(db_session) == ["IGNORED"]


def test_an_event_about_a_payment_we_do_not_know_is_kept_for_a_person_to_see(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    response = post_webhook(
        api, event({"provider_transaction_id": "txn_never_seen", "amount_paise": 100})
    )
    assert response.status_code == 200
    stored = db_session.scalar(select(PaymentEvent))
    assert (stored.processing_status, stored.payment_id) == ("FAILED", None)
    assert "no payment" in stored.error
    assert "payment.webhook_failed" in caplog.text


@pytest.mark.parametrize("wrong", [{"amount_paise": 1}, {"currency": "USD"}])
@pytest.mark.parametrize("type_", ["payment.succeeded", "payment.failed"])
def test_the_wrong_amount_changes_nothing(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    wrong: dict[str, Any],
    type_: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, order, intent = paying(api, db_session)

    assert post_webhook(api, event(intent, type=type_, **wrong)).status_code == 200

    assert statuses(db_session, order) == UNTOUCHED
    stored = db_session.scalar(select(PaymentEvent))
    assert stored.processing_status == "FAILED"
    assert "mismatch" in stored.error
    assert "payment.webhook_failed" in caplog.text


def test_an_event_that_cannot_be_applied_is_kept_and_answered_200(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    # A row in a state no code path produces. The gateway must not retry for ever over it.
    _, order, intent = paying(api, db_session)
    set_row(db_session, Reservation, order["reservation_id"], status="EXPIRED")

    assert post_webhook(api, event(intent)).status_code == 200

    assert statuses(db_session, order) == ("PAYMENT_PENDING", "EXPIRED", "RESERVED", "PENDING")
    stored = db_session.scalar(select(PaymentEvent))
    assert stored.processing_status == "FAILED"
    assert "illegal-reservation-transition" in stored.error


# ---------------------------------------------------------------- forgeries


def test_a_forged_signature_is_401_and_changes_nothing(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    _, order, intent = paying(api, db_session)

    response = post_webhook(api, event(intent), secret="a guess")

    assert response.status_code == 401
    assert problem_code(response) == "bad-webhook-signature"
    assert statuses(db_session, order) == UNTOUCHED
    assert count(db_session, PaymentEvent) == 0


def test_a_changed_body_is_401(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    _, order, intent = paying(api, db_session)
    body = event(intent)
    real = post_webhook(api, body, secret="irrelevant")  # any request, to borrow its headers
    signature = real.request.headers["X-Fakepay-Signature"]
    response = post_webhook(
        api, body | {"amount_paise": 1}, headers={"X-Fakepay-Signature": signature}
    )
    assert response.status_code == 401
    assert statuses(db_session, order) == UNTOUCHED


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> int:
    """The server's clock, stopped on a whole second, so a test can stand exactly on the edge."""
    now = int(time.time())
    monkeypatch.setattr(webhooks, "now", lambda: datetime.fromtimestamp(now, UTC))
    return now


@pytest.mark.parametrize("age", [301, 3600, -301])
def test_a_stale_or_future_timestamp_is_401(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    frozen_now: int,
    age: int,
) -> None:
    _, order, intent = paying(api, db_session)
    response = post_webhook(api, event(intent), timestamp=str(frozen_now - age))
    assert response.status_code == 401
    assert statuses(db_session, order) == UNTOUCHED


def test_a_captured_webhook_cannot_be_given_a_new_timestamp(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    _, order, intent = paying(api, db_session)
    body = event(intent)
    old = post_webhook(api, body, timestamp=str(int(time.time()) - 600))
    response = post_webhook(
        api, body, headers={"X-Fakepay-Signature": old.request.headers["X-Fakepay-Signature"]}
    )
    assert response.status_code == 401
    assert statuses(db_session, order) == UNTOUCHED


@pytest.mark.parametrize("age", [0, 300, -300])
def test_up_to_five_minutes_either_way_is_accepted(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    frozen_now: int,
    age: int,
) -> None:
    _, order, intent = paying(api, db_session)
    response = post_webhook(api, event(intent), timestamp=str(frozen_now - age))
    assert response.status_code == 200
    assert statuses(db_session, order) == PAID


@pytest.mark.parametrize(
    "timestamp", ["", "soon", "-5", "1e9", "9" * 400, "٣٣٣"], ids=lambda t: t[:8]
)
def test_a_timestamp_that_is_not_one_is_401(api: TestClient, timestamp: str) -> None:
    if timestamp.isascii():
        assert post_webhook(api, b"{}", timestamp=timestamp).status_code == 401
    else:
        headers = {
            b"x-fakepay-timestamp": timestamp.encode(),
            b"x-fakepay-signature": b"0" * 64,
        }
        assert api.post(WEBHOOK_PATH, content=b"{}", headers=headers).status_code == 401


@pytest.mark.parametrize("missing", ["X-Fakepay-Signature", "X-Fakepay-Timestamp"])
def test_a_missing_header_is_401(api: TestClient, missing: str) -> None:
    headers = {"X-Fakepay-Signature": "0" * 64, "X-Fakepay-Timestamp": str(int(time.time()))}
    headers.pop(missing)
    assert api.post(WEBHOOK_PATH, content=b"{}", headers=headers).status_code == 401


def test_a_signature_that_is_not_text_is_401(api: TestClient) -> None:
    headers = {
        b"x-fakepay-timestamp": str(int(time.time())).encode(),
        b"x-fakepay-signature": "é".encode(),
    }
    assert api.post(WEBHOOK_PATH, content=b"{}", headers=headers).status_code == 401


# ---------------------------------------------------------------- bodies


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"[]",
        b"{}",
        b'{"event_id": "evt"}',
        b"\xff\xfe",
        b'{"event_id": "a\\u0000b", "type": "payment.succeeded", "transaction_id": "t",'
        b' "amount_paise": 1, "currency": "INR", "occurred_at": "2026-11-26T10:00:00Z"}',
    ],
)
def test_a_signed_body_we_cannot_read_is_400(
    api: TestClient, db_session: Session, body: bytes
) -> None:
    response = post_webhook(api, body)
    assert response.status_code == 400
    assert problem_code(response) == "bad-webhook-body"
    assert count(db_session, PaymentEvent) == 0


def test_extra_fields_and_odd_spacing_are_fine(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    import json

    _, order, intent = paying(api, db_session)
    raw = json.dumps(event(intent, livemode=False), indent=4).encode()
    assert post_webhook(api, raw).status_code == 200
    assert statuses(db_session, order) == PAID


def test_another_provider_is_404(api: TestClient) -> None:
    response = api.post("/api/v1/payments/webhooks/realpay", content=b"{}")
    assert response.status_code == 404
    assert problem_code(response) == "unknown-payment-provider"


def test_an_enormous_body_is_refused_before_it_is_read(api: TestClient) -> None:
    response = post_webhook(api, b"x" * (7 * 1024 * 1024))
    assert response.status_code == 413
