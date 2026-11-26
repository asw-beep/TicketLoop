"""Starting to pay: POST /payments/intents, with fakepay answering, silent, or saying no."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.idempotency.models import IdempotencyKeyRow
from app.modules.listings.models import Listing
from app.modules.orders.models import Order
from app.modules.payments.models import Payment
from app.modules.reservations.models import Reservation
from app.modules.reservations.service import expire_reservations
from tests.integration.api import bearer, problem_code
from tests.integration.payments import (
    Fakepay,
    fakepay,  # noqa: F401
    ordered,
    paying,
    post_intent,
)
from tests.integration.test_orders import audit, set_row

pytestmark = pytest.mark.integration


def statuses(db: Session, order: dict[str, object]) -> tuple[str, ...]:
    """(order, reservation, listing, its payments...)"""
    db.expire_all()
    return (
        db.get(Order, uuid.UUID(str(order["id"]))).status,
        db.get(Reservation, uuid.UUID(str(order["reservation_id"]))).status,
        db.get(Listing, uuid.UUID(str(order["listing_id"]))).status,
        *db.scalars(
            select(Payment.status)
            .where(Payment.order_id == uuid.UUID(str(order["id"])))
            .order_by(Payment.created_at)
        ),
    )


def count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model))


# ---------------------------------------------------------------- the happy path


def test_starting_to_pay_says_where_to_go(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    buyer, order = ordered(api, db_session)

    response = post_intent(api, order["id"], buyer)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "payment_id",
        "provider",
        "provider_transaction_id",
        "checkout_url",
        "amount_paise",
        "currency",
        "status",
    }
    assert (body["status"], body["provider"]) == ("PENDING", "fakepay")
    assert body["amount_paise"] == order["amount_paise"]
    assert body["checkout_url"].endswith(f"/checkout/{body['provider_transaction_id']}")
    assert statuses(db_session, order) == (
        "PAYMENT_PENDING",
        "PAYMENT_PENDING",
        "RESERVED",
        "PENDING",
    )
    actor = uuid.UUID(buyer["id"])
    assert audit(db_session, body["payment_id"]) == [
        ("payment.created", actor),
        ("payment.pending", None),
    ]
    assert audit(db_session, order["id"])[-1] == ("order.payment_pending", actor)


def test_the_gateway_is_asked_for_the_orders_amount_under_the_payments_own_id(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    _, order, intent = paying(api, db_session)
    made = fakepay.client.get(f"/v1/intents?idempotency_key={intent['payment_id']}").json()
    assert made["transaction_id"] == intent["provider_transaction_id"]
    assert made["amount_paise"] == order["amount_paise"]
    # The buyer comes back to the order's page.
    page = fakepay.client.post(
        f"/checkout/{made['transaction_id']}",
        data={"outcome": "no_webhook"},
        follow_redirects=False,
    )
    assert page.headers["location"] == f"http://localhost:5173/orders/{order['id']}"


def test_the_same_click_gets_the_same_answer(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    buyer, order = ordered(api, db_session)
    first = post_intent(api, order["id"], buyer, "click")
    again = post_intent(api, order["id"], buyer, "click")

    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json() == first.json()
    assert again.headers["Idempotent-Replayed"] == "true"
    assert fakepay.calls == ["POST /v1/intents"]


def test_coming_back_later_continues_the_same_payment(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    buyer, order, intent = paying(api, db_session)

    again = post_intent(api, order["id"], buyer)  # another click, another key

    assert again.status_code == 200
    assert again.json() == intent
    assert count(db_session, Payment) == 1


# ---------------------------------------------------------------- refusals


@pytest.mark.parametrize("status", ["PAID", "PAYMENT_FAILED", "CANCELLED", "COMPLETED"])
def test_an_order_that_is_over_cannot_be_paid_for(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    status: str,
) -> None:
    buyer, order = ordered(api, db_session)
    set_row(db_session, Order, order["id"], status=status)

    response = post_intent(api, order["id"], buyer)

    assert response.status_code == 409
    assert problem_code(response) == "order-not-payable"
    assert response.json()["current_status"] == status
    assert fakepay.calls == []


def test_a_hold_that_ran_out_cannot_be_paid_for(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    buyer, order = ordered(api, db_session)
    past = datetime.now(UTC) - timedelta(seconds=1)
    set_row(db_session, Reservation, order["reservation_id"], expires_at=past)

    response = post_intent(api, order["id"], buyer, "k")

    assert response.status_code == 409
    assert problem_code(response) == "reservation-not-payable"
    assert response.json()["reason"] == "hold-ended"
    assert statuses(db_session, order) == ("CREATED", "CREATED", "RESERVED")
    assert fakepay.calls == []
    # Nothing was kept, the key included.
    rows = select(func.count()).select_from(IdempotencyKeyRow).where(IdempotencyKeyRow.key == "k")
    assert db_session.scalar(rows) == 0


def test_paying_takes_the_hold_out_of_the_clocks_reach(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    buyer, order, _ = paying(api, db_session)

    later = datetime.now(UTC) + timedelta(hours=1)
    assert expire_reservations(db_session, now=later) == (0, 0)

    cancel = api.post(
        f"/api/v1/reservations/{order['reservation_id']}/cancel", headers=bearer(buyer["token"])
    )
    assert problem_code(cancel) == "reservation-not-cancellable"
    assert statuses(db_session, order)[1:3] == ("PAYMENT_PENDING", "RESERVED")


# ---------------------------------------------------------------- the gateway misbehaves


def test_the_gateway_is_down_and_the_next_try_continues(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    buyer, order = ordered(api, db_session)
    fakepay.down = True

    response = post_intent(api, order["id"], buyer, "click")

    assert response.status_code == 503
    assert problem_code(response) == "payment-gateway-unavailable"
    assert response.headers["Retry-After"] == "5"
    # The ticket is kept for this buyer: money might have moved, for all we know.
    assert statuses(db_session, order) == (
        "PAYMENT_PENDING",
        "PAYMENT_PENDING",
        "RESERVED",
        "CREATED",
    )

    fakepay.down = False
    again = post_intent(api, order["id"], buyer, "click")  # the same click, sent again

    assert again.status_code == 200
    assert "Idempotent-Replayed" not in again.headers
    assert statuses(db_session, order)[3:] == ("PENDING",)


def test_the_answer_is_lost_and_the_next_try_finds_the_same_intent(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    buyer, order = ordered(api, db_session)
    fakepay.answers_lost = True
    assert post_intent(api, order["id"], buyer).status_code == 503
    payment_id = db_session.scalar(select(Payment.id))
    lost = fakepay.client.get(f"/v1/intents?idempotency_key={payment_id}").json()

    fakepay.answers_lost = False
    again = post_intent(api, order["id"], buyer)

    assert again.json()["provider_transaction_id"] == lost["transaction_id"]
    assert count(db_session, Payment) == 1


def test_the_gateway_says_no_and_the_ticket_goes_back_on_sale(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    buyer, order = ordered(api, db_session)
    # fakepay refuses a key it has seen for another amount.
    set_row(db_session, Order, order["id"], amount_paise=1)
    fakepay.client.post = None  # type: ignore[method-assign]  # nothing else may be called
    fakepay.request = lambda method, path, **kwargs: fakepay.client.request(  # type: ignore[method-assign]
        method, path, **(kwargs | {"json": {"amount_paise": 0}})
    )

    response = post_intent(api, order["id"], buyer)

    assert response.status_code == 502
    assert problem_code(response) == "payment-refused"
    assert statuses(db_session, order) == ("PAYMENT_FAILED", "FAILED", "ACTIVE", "FAILED")
    assert db_session.scalar(select(Payment.failure_reason)) == "refused"


def test_a_payment_settled_while_the_gateway_was_answering_sends_nobody_to_pay(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    # The expiry job gave up on it in between. Its checkout page must not be handed out.
    buyer, order = ordered(api, db_session)
    ask = fakepay.request

    def slow(method: str, path: str, **kwargs: object):
        from app.modules.payments import service

        response = ask(method, path, **kwargs)
        payment_id = db_session.scalar(select(Payment.id))
        service.fail(db_session, payment_id, reason="intent_never_created", source="expiry")
        db_session.commit()
        return response

    fakepay.request = slow  # type: ignore[method-assign]

    response = post_intent(api, order["id"], buyer)

    assert response.status_code == 409
    assert problem_code(response) == "order-not-payable"
    assert response.json()["current_status"] == "PAYMENT_FAILED"
    assert "checkout_url" not in response.text
    assert statuses(db_session, order) == ("PAYMENT_FAILED", "FAILED", "ACTIVE", "FAILED")


def test_a_request_that_is_still_asking_the_gateway_holds_its_key(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    buyer, order = ordered(api, db_session)
    inner: list[int] = []
    ask = fakepay.request

    def slow(method: str, path: str, **kwargs: object):
        if not inner:
            inner.append(0)
            twin = post_intent(api, order["id"], buyer, "click")
            inner.append(twin.status_code)
            assert problem_code(twin) == "idempotency-key-in-progress"
            assert twin.headers["Retry-After"] == "2"
        return ask(method, path, **kwargs)

    fakepay.request = slow  # type: ignore[method-assign]

    assert post_intent(api, order["id"], buyer, "click").status_code == 201
    assert inner == [0, 409]
    assert fakepay.calls == ["POST /v1/intents"]


def test_a_key_whose_process_died_is_taken_over_after_two_minutes(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    buyer, order = ordered(api, db_session)
    fakepay.down = True
    post_intent(api, order["id"], buyer, "click")
    fakepay.down = False

    def lock_taken(seconds_ago: int) -> None:
        db_session.execute(
            update(IdempotencyKeyRow)
            .where(IdempotencyKeyRow.key == "click")
            .values(locked_at=func.clock_timestamp() - timedelta(seconds=seconds_ago))
        )
        db_session.commit()

    lock_taken(60)
    assert post_intent(api, order["id"], buyer, "click").status_code == 409
    lock_taken(121)
    assert post_intent(api, order["id"], buyer, "click").status_code == 200


# ---------------------------------------------------------------- coming back to an old intent

PAYING = ("PAYMENT_PENDING", "PAYMENT_PENDING", "RESERVED", "PENDING")


def test_coming_back_to_an_intent_nobody_paid_in_time_ends_it(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    # Its page would refuse the money now. Handing it out again would keep the ticket from
    # everyone else for as long as the buyer kept coming back.
    buyer, order, _ = paying(api, db_session)
    fakepay.now += 16 * 60

    response = post_intent(api, order["id"], buyer)

    assert response.status_code == 409
    assert problem_code(response) == "order-not-payable"
    assert response.json()["current_status"] == "CANCELLED"
    assert "checkout_url" not in response.text
    assert statuses(db_session, order) == ("CANCELLED", "EXPIRED", "ACTIVE", "FAILED")


def test_coming_back_to_an_intent_that_was_paid_settles_it(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    buyer, order, intent = paying(api, db_session)
    fakepay.choose(intent["provider_transaction_id"], "no_webhook")

    response = post_intent(api, order["id"], buyer)

    assert response.status_code == 409
    assert response.json()["current_status"] == "PAID"
    assert statuses(db_session, order) == ("PAID", "CONFIRMED", "RESERVED", "SUCCEEDED")


@pytest.mark.parametrize("before", ["sent-to-pay", "answer-lost"])
def test_a_no_for_a_payment_that_may_have_an_intent_keeps_the_ticket_held(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    before: str,
) -> None:
    # The intent made the first time may still take the money on its page. Putting the ticket
    # back on sale now could sell it twice.
    if before == "sent-to-pay":
        buyer, order, _ = paying(api, db_session)
    else:
        buyer, order = ordered(api, db_session)
        fakepay.answers_lost = True
        post_intent(api, order["id"], buyer)
        fakepay.answers_lost = False
    was = statuses(db_session, order)
    fakepay.request = lambda method, path, **kwargs: httpx.Response(422)  # type: ignore[method-assign]

    response = post_intent(api, order["id"], buyer)

    assert response.status_code == 503
    assert problem_code(response) == "payment-gateway-unavailable"
    assert statuses(db_session, order) == was
    assert "payment.refused_but_kept" in caplog.text


def test_a_no_for_a_payment_settled_meanwhile_says_it_is_over(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    buyer, order = ordered(api, db_session)

    def no(method: str, path: str, **kwargs: object) -> httpx.Response:
        from app.modules.payments import service

        payment_id = db_session.scalar(select(Payment.id))
        service.fail(db_session, payment_id, reason="intent_never_created", source="expiry")
        db_session.commit()
        return httpx.Response(422)

    fakepay.request = no  # type: ignore[method-assign]

    response = post_intent(api, order["id"], buyer)

    assert response.status_code == 409
    assert response.json()["current_status"] == "PAYMENT_FAILED"


def test_an_intent_that_is_not_this_payments_sends_nobody_to_pay(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    # We kept one transaction id and the gateway now answers with another: the money could go
    # to an intent we would never look at.
    buyer, order, intent = paying(api, db_session)
    set_row(
        db_session, Payment, uuid.UUID(intent["payment_id"]), provider_transaction_id="txn_ours"
    )
    db_session.commit()

    response = post_intent(api, order["id"], buyer)

    assert response.status_code == 503
    assert "checkout_url" not in response.text
    assert "payment.intent_mismatch" in caplog.text
    assert statuses(db_session, order) == PAYING
    assert db_session.scalar(select(Payment.provider_transaction_id)) == "txn_ours"
