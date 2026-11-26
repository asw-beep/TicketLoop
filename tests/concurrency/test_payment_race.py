"""M8's races: the same click fifty times, the same webhook ten times, and paying against
everything that could take the ticket away. Four API processes, fakepay in a fifth.

Every round ends by reading the four rows a payment touches, which must tell one story
(`story()`): a ticket that was paid for is never back on sale, and one that was not is never kept.
"""

import os
import random
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx2 as httpx
import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.payments import GatewayIntent
from app.modules.listings.models import Listing
from app.modules.orders.models import Order
from app.modules.payments.models import Payment, PaymentEvent
from app.modules.payments.reconcile import reconcile, resolve_overdue
from app.modules.reservations.models import Reservation
from app.modules.reservations.service import expire_reservations
from tests.concurrency.harness import (
    ApiServer,
    Buyer,
    Call,
    after,
    audit_counts,
    fire_together,
    make_active_listing,
    make_buyers,
)
from tests.integration.factories import make_order, make_payment, make_reservation
from tests.integration.payments import event, signed

pytestmark = pytest.mark.integration

ROUNDS = 5
DUEL_ROUNDS = 10
# The spec's number for "expiry never releases a paid ticket".
PAID_ROUNDS = int(os.environ.get("RACE_PAID_ROUNDS", "50"))

NOTHING_PAID = ("CANCELLED", "CANCELLED", "ACTIVE")
EXPIRED = ("CANCELLED", "EXPIRED", "ACTIVE")
PAYING = ("PAYMENT_PENDING", "PAYMENT_PENDING", "RESERVED", "PENDING")
PAID = ("PAID", "CONFIRMED", "RESERVED", "SUCCEEDED")
DECLINED = ("PAYMENT_FAILED", "FAILED", "ACTIVE", "FAILED")
TIMED_OUT = ("CANCELLED", "EXPIRED", "ACTIVE", "FAILED")


@pytest.fixture(scope="module")
def buyer(race_engine: Engine, race_settings: Settings) -> Buyer:
    return make_buyers(race_engine, race_settings, 1)[0]


def make_hold(engine: Engine, buyer: Buyer) -> Reservation:
    listing = make_active_listing(engine)
    with Session(engine, expire_on_commit=False) as session, session.begin():
        session.execute(update(Listing).where(Listing.id == listing.id).values(status="RESERVED"))
        return make_reservation(session, listing=listing, buyer_id=buyer.id)


def make_unpaid(engine: Engine, buyer: Buyer) -> Order:
    hold = make_hold(engine, buyer)
    with Session(engine, expire_on_commit=False) as session, session.begin():
        return make_order(session, reservation=session.get_one(Reservation, hold.id))


def make_paying(engine: Engine, buyer: Buyer, hold_ended: timedelta = timedelta()) -> Payment:
    """A buyer at the gateway's checkout, whose hold ended this long ago."""
    hold = make_hold(engine, buyer)
    with Session(engine, expire_on_commit=False) as session, session.begin():
        reservation = session.get_one(Reservation, hold.id)
        reservation.status = "PAYMENT_PENDING"
        reservation.expires_at = datetime.now(UTC) - hold_ended
        order = make_order(session, reservation=reservation, status="PAYMENT_PENDING")
        payment = make_payment(session, order=order, status="PENDING")
        payment.updated_at = datetime.now(UTC) - timedelta(minutes=10)
        session.flush()
        session.expunge_all()
        return payment


def story(engine: Engine, order_id: uuid.UUID) -> tuple[str, ...]:
    """(order, reservation, listing, its payments...)"""
    with Session(engine) as session:
        order = session.get_one(Order, order_id)
        payments = session.scalars(select(Payment.status).where(Payment.order_id == order_id))
        return (
            order.status,
            session.get_one(Reservation, order.reservation_id).status,
            session.get_one(Listing, order.listing_id).status,
            *payments,
        )


def post_order(buyer: Buyer, reservation_id: uuid.UUID, key: str) -> Call:
    return lambda client: client.post(
        "/api/v1/orders",
        json={"reservation_id": str(reservation_id)},
        headers=buyer.headers | {"Idempotency-Key": key},
    )


def post_intent(buyer: Buyer, order_id: uuid.UUID, key: str) -> Call:
    return lambda client: client.post(
        "/api/v1/payments/intents",
        json={"order_id": str(order_id)},
        headers=buyer.headers | {"Idempotency-Key": key},
    )


def cancel_hold(buyer: Buyer, reservation_id: uuid.UUID) -> Call:
    return lambda client: client.post(
        f"/api/v1/reservations/{reservation_id}/cancel", headers=buyer.headers
    )


def webhook(
    payment: Payment, type_: str = "payment.succeeded", event_id: str | None = None
) -> Call:
    body = event(
        {
            "provider_transaction_id": payment.provider_transaction_id,
            "amount_paise": payment.amount_paise,
        },
        type=type_,
        event_id=event_id or f"evt_{uuid.uuid4().hex}",
    )
    return lambda client: client.post("/api/v1/payments/webhooks/fakepay", **signed(body))


def key() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------- one click, many times


def test_fifty_orders_with_one_key_are_one_order_and_fifty_identical_answers(
    api_server: ApiServer, race_engine: Engine, buyer: Buyer
) -> None:
    for _ in range(ROUNDS):
        hold = make_hold(race_engine, buyer)

        replies = fire_together(api_server.urls, [post_order(buyer, hold.id, "click" + key())] * 50)

        assert Counter(r.status for r in replies) == {201: 50}
        assert len({str(r.body) for r in replies}) == 1
        assert Counter(r.replayed for r in replies) == {False: 1, True: 49}
        with Session(race_engine) as session:
            orders = session.scalars(select(Order).where(Order.reservation_id == hold.id)).all()
            assert [str(o.id) for o in orders] == [replies[0].body["id"]]
            assert audit_counts(session, orders[0].id) == {"order.created": 1}


def test_fifty_orders_with_fifty_keys_are_still_one_order(
    api_server: ApiServer, race_engine: Engine, buyer: Buyer
) -> None:
    for _ in range(ROUNDS):
        hold = make_hold(race_engine, buyer)

        replies = fire_together(
            api_server.urls, [post_order(buyer, hold.id, key()) for _ in range(50)]
        )

        assert Counter(r.status for r in replies) == {201: 1, 409: 49}
        (made,) = [r.body["id"] for r in replies if r.status == 201]
        assert {r.body["order_id"] for r in replies if r.status == 409} == {made}


@pytest.mark.usefixtures("fakepay_server")
def test_ten_tabs_starting_to_pay_are_one_payment(
    api_server: ApiServer, race_engine: Engine, buyer: Buyer
) -> None:
    for _ in range(ROUNDS):
        order = make_unpaid(race_engine, buyer)

        replies = fire_together(
            api_server.urls, [post_intent(buyer, order.id, key()) for _ in range(10)]
        )

        assert Counter(r.status for r in replies) == {201: 1, 200: 9}
        assert len({r.body["provider_transaction_id"] for r in replies}) == 1
        assert story(race_engine, order.id) == PAYING


@pytest.mark.usefixtures("fakepay_server")
def test_one_click_to_pay_sent_ten_times_is_one_payment(
    api_server: ApiServer, race_engine: Engine, buyer: Buyer
) -> None:
    for _ in range(ROUNDS):
        order = make_unpaid(race_engine, buyer)

        replies = fire_together(api_server.urls, [post_intent(buyer, order.id, key())] * 10)

        # The first is asking the gateway; whoever comes meanwhile is told to wait.
        statuses = Counter(r.status for r in replies)
        assert set(statuses) <= {201, 409} and statuses[201] >= 1
        assert {r.body["type"].rsplit(":", 1)[-1] for r in replies if r.status == 409} <= {
            "idempotency-key-in-progress"
        }
        assert sum(not r.replayed for r in replies if r.status == 201) == 1
        assert story(race_engine, order.id) == PAYING


# ---------------------------------------------------------------- webhooks at once


def test_one_webhook_delivered_ten_times_at_once_counts_once(
    api_server: ApiServer, race_engine: Engine, buyer: Buyer
) -> None:
    for _ in range(ROUNDS):
        payment = make_paying(race_engine, buyer)
        event_id = f"evt_{key()}"

        replies = fire_together(api_server.urls, [webhook(payment, event_id=event_id)] * 10)

        assert Counter(r.status for r in replies) == {200: 10}
        assert story(race_engine, payment.order_id) == PAID
        with Session(race_engine) as session:
            assert audit_counts(session, payment.id) == {"payment.succeeded": 1}
            assert audit_counts(session, payment.order_id) == {"order.paid": 1}
            events = session.scalar(
                select(func.count())
                .select_from(PaymentEvent)
                .where(PaymentEvent.provider_event_id == event_id)
            )
            assert events == 1


def test_a_success_and_a_failure_at_once_tell_one_story(
    api_server: ApiServer, race_engine: Engine, buyer: Buyer
) -> None:
    for _ in range(DUEL_ROUNDS):
        payment = make_paying(race_engine, buyer)
        calls = [webhook(payment, t) for t in ("payment.succeeded", "payment.failed")] * 3
        random.shuffle(calls)

        replies = fire_together(api_server.urls, calls)

        assert Counter(r.status for r in replies) == {200: 6}
        assert story(race_engine, payment.order_id) in (PAID, DECLINED)
        with Session(race_engine) as session:
            assert sum(audit_counts(session, payment.id).values()) == 1


# ---------------------------------------------------------------- paying against giving up


def test_ordering_against_giving_up_the_hold(
    api_server: ApiServer, race_engine: Engine, buyer: Buyer
) -> None:
    outcomes: Counter[int] = Counter()
    for round_ in range(DUEL_ROUNDS):
        hold = make_hold(race_engine, buyer)

        ordered, cancelled = fire_together(
            api_server.urls,
            [
                post_order(buyer, hold.id, key()),
                after(round_ * 0.01, cancel_hold(buyer, hold.id)),
            ],
        )

        assert cancelled.status == 200
        if ordered.status == 201:
            assert story(race_engine, uuid.UUID(str(ordered.body["id"]))) == NOTHING_PAID
        else:
            assert ordered.status == 409, ordered.body
        outcomes[ordered.status] += 1
    assert outcomes[201], f"the order never won: {outcomes}"


@pytest.mark.usefixtures("fakepay_server")
def test_starting_to_pay_against_giving_up_the_hold(
    api_server: ApiServer, race_engine: Engine, buyer: Buyer
) -> None:
    outcomes: Counter[str] = Counter()
    for round_ in range(DUEL_ROUNDS):
        order = make_unpaid(race_engine, buyer)

        paid, cancelled = fire_together(
            api_server.urls,
            [
                after(round_ * 0.01, post_intent(buyer, order.id, key())),
                after(0.03, cancel_hold(buyer, order.reservation_id)),
            ],
        )

        if cancelled.status == 200:
            assert paid.status == 409, paid.body
            assert story(race_engine, order.id) == NOTHING_PAID
        else:
            assert (paid.status, cancelled.status) == (201, 409), (paid.body, cancelled.body)
            assert story(race_engine, order.id) == PAYING
        outcomes["cancelled" if cancelled.status == 200 else "paying"] += 1
    assert len(outcomes) == 2, f"only one side ever won: {outcomes}"


@pytest.mark.usefixtures("fakepay_server")
def test_starting_to_pay_against_the_clock(
    api_server: ApiServer, race_engine: Engine, buyer: Buyer
) -> None:
    outcomes: Counter[str] = Counter()
    for round_ in range(DUEL_ROUNDS):
        order = make_unpaid(race_engine, buyer)

        def expire(_: httpx.Client) -> httpx.Response:
            with Session(race_engine) as session, session.begin():
                found = expire_reservations(session, now=datetime.now(UTC) + timedelta(hours=1))
            return httpx.Response(200, json={"found": found})

        paid, _ = fire_together(
            api_server.urls,
            [after(round_ * 0.01, post_intent(buyer, order.id, key())), after(0.03, expire)],
        )

        if paid.status == 201:
            assert story(race_engine, order.id) == PAYING
        else:
            assert paid.status == 409, paid.body
            assert story(race_engine, order.id) == EXPIRED
        outcomes["paying" if paid.status == 201 else "expired"] += 1
    assert len(outcomes) == 2, f"only one side ever won: {outcomes}"


# ---------------------------------------------------------------- paid against timing out


class Says:
    """A gateway that answers the same about every payment, a little late."""

    name = "says"

    def __init__(self, status: str, seconds: float) -> None:
        self._status = status
        self._seconds = seconds

    def by_transaction(self, transaction_id: str) -> GatewayIntent:
        time.sleep(self._seconds)
        return GatewayIntent(transaction_id, self._status, 250_000, "INR", "", {})  # type: ignore[arg-type]

    def by_idempotency_key(self, idempotency_key: str) -> GatewayIntent:
        raise AssertionError("every payment here has a transaction id")


def test_a_paid_ticket_is_never_released_by_the_expiry_job(
    api_server: ApiServer, race_engine: Engine, buyer: Buyer
) -> None:
    """The worst case: the gateway's answer "it expired" is already on its way when the webhook
    saying "paid" arrives. Whoever gets the reservation's lock first decides, and all four rows
    agree. A real gateway never says both; this is what protects us if one does."""
    sessions = create_session_factory(race_engine)
    outcomes: Counter[tuple[str, ...]] = Counter()
    for round_ in range(PAID_ROUNDS):
        payment = make_paying(race_engine, buyer, hold_ended=timedelta(minutes=16))
        gateway = Says("expired", seconds=0.02)

        def expire(_: httpx.Client, gateway: Says = gateway) -> httpx.Response:
            with sessions() as session, session.begin():
                expire_reservations(session)  # the clock's own job never touches it
            report = resolve_overdue(sessions, gateway)  # type: ignore[arg-type]
            assert report.error is None
            return httpx.Response(200, json={"fixed": report.fixed})

        late = (round_ % 10) * 0.008  # from well before the gateway's answer to well after
        paid, expired = fire_together(api_server.urls, [after(late, webhook(payment)), expire])

        assert (paid.status, expired.status) == (200, 200)
        end = story(race_engine, payment.order_id)
        assert end in (PAID, TIMED_OUT), end
        outcomes[end] += 1
        with Session(race_engine) as session:
            assert sum(audit_counts(session, payment.id).values()) == 1
    assert set(outcomes) == {PAID, TIMED_OUT}, f"only one side ever won: {outcomes}"


def test_reconciliation_twice_and_the_webhook_settle_a_payment_once(
    api_server: ApiServer, race_engine: Engine, buyer: Buyer
) -> None:
    sessions = create_session_factory(race_engine)
    for _ in range(DUEL_ROUNDS):
        payment = make_paying(race_engine, buyer)

        def repair(_: httpx.Client) -> httpx.Response:
            run = reconcile(sessions, Says("succeeded", 0.0))  # type: ignore[arg-type]
            return httpx.Response(200, json={"fixed": run.fixed_count, "error": run.error})

        replies = fire_together(
            api_server.urls, [webhook(payment), after(0.005, repair), after(0.01, repair)]
        )

        assert {r.status for r in replies} == {200}
        assert story(race_engine, payment.order_id) == PAID
        with Session(race_engine) as session:
            assert audit_counts(session, payment.id) == {"payment.succeeded": 1}
            assert audit_counts(session, payment.order_id) == {"order.paid": 1}
