"""Every (from, to) pair of order and payment statuses, like the reservations' test, and the rules
the database enforces on both tables."""

import itertools

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.modules.audit.models import AuditLog
from app.modules.orders import state as order_state
from app.modules.orders.errors import IllegalOrderTransitionError, OrderChangedError
from app.modules.orders.models import Order, OrderStatus
from app.modules.payments import state as payment_state
from app.modules.payments.errors import IllegalPaymentTransitionError, PaymentChangedError
from app.modules.payments.models import Payment, PaymentStatus
from tests.integration.factories import (
    make_listing,
    make_order,
    make_payment,
    make_payment_event,
    make_reservation,
)

pytestmark = pytest.mark.integration

# docs/state-machines.md, written out by hand: changing either side alone fails the test.
ORDER_TABLE = {
    "CREATED": {"PAYMENT_PENDING", "CANCELLED"},
    "PAYMENT_PENDING": {"PAID", "PAYMENT_FAILED", "CANCELLED"},
    "PAID": {"TRANSFER_PENDING"},
    "TRANSFER_PENDING": {"COMPLETED", "TRANSFER_FAILED"},
    "PAYMENT_FAILED": set(),
    "CANCELLED": set(),
    "COMPLETED": set(),
    "TRANSFER_FAILED": set(),
}
PAYMENT_TABLE = {
    "CREATED": {"PENDING", "FAILED"},
    "PENDING": {"SUCCEEDED", "FAILED"},
    "SUCCEEDED": set(),
    "FAILED": set(),
}


def test_allowed_matches_docs_state_machines_md():
    for allowed, table in (
        (order_state.ALLOWED, ORDER_TABLE),
        (payment_state.ALLOWED, PAYMENT_TABLE),
    ):
        assert {k.value: {s.value for s in v} for k, v in allowed.items()} == table


def check_move(db, row, entity_type, state, illegal, from_, to):
    rows = select(AuditLog).where(AuditLog.entity_type == entity_type, AuditLog.entity_id == row.id)
    if to in state.ALLOWED[from_]:
        state.transition(db, row, to, actor_id=None)
        assert row.status == to
        (audit,) = db.scalars(rows)
        assert audit.action == state.ACTION[(from_, to)]
        assert audit.details["from"] == from_ and audit.details["to"] == to
    else:
        with pytest.raises(illegal):
            state.transition(db, row, to, actor_id=None)
        db.refresh(row)
        assert row.status == from_
        assert db.scalar(select(func.count()).select_from(rows.subquery())) == 0


@pytest.mark.parametrize("from_, to", list(itertools.product(OrderStatus, OrderStatus)))
def test_only_allowed_order_moves_succeed(db_session, from_, to):
    order = make_order(db_session, status=from_)
    check_move(db_session, order, "order", order_state, IllegalOrderTransitionError, from_, to)


@pytest.mark.parametrize("from_, to", list(itertools.product(PaymentStatus, PaymentStatus)))
def test_only_allowed_payment_moves_succeed(db_session, from_, to):
    payment = make_payment(db_session, status=from_)
    check_move(
        db_session, payment, "payment", payment_state, IllegalPaymentTransitionError, from_, to
    )


def test_changed_underneath(db_session):
    order = make_order(db_session, status="CREATED")
    payment = make_payment(db_session, status="PENDING")
    for model, row, status in ((Order, order, "CANCELLED"), (Payment, payment, "FAILED")):
        db_session.execute(
            update(model).where(model.id == row.id).values(status=status),
            execution_options={"synchronize_session": False},
        )
    with pytest.raises(OrderChangedError) as caught:
        order_state.transition(db_session, order, OrderStatus.PAYMENT_PENDING, actor_id=None)
    assert caught.value.extensions["current_status"] == "CANCELLED"
    with pytest.raises(PaymentChangedError) as caught:
        payment_state.transition(db_session, payment, PaymentStatus.SUCCEEDED)
    assert caught.value.extensions["current_status"] == "FAILED"


# ---------------------------------------------------------------- the database's own rules


def test_a_reservation_has_one_order(db_session):
    reservation = make_reservation(db_session)
    make_order(db_session, reservation=reservation, status="CANCELLED")
    with pytest.raises(IntegrityError, match="uq_orders_reservation"):
        make_order(db_session, reservation=reservation)


@pytest.mark.parametrize(
    "fields, rule",
    [
        ({"status": "MAYBE"}, "ck_orders_status"),
        ({"amount_paise": 0}, "ck_orders_amount"),
        ({"currency": "USD"}, "ck_orders_currency"),
    ],
)
def test_what_an_order_cannot_be(db_session, fields, rule):
    with pytest.raises(IntegrityError, match=rule):
        make_order(db_session, **fields)


def test_nobody_orders_from_themselves(db_session):
    listing = make_listing(db_session, status="RESERVED")
    with pytest.raises(IntegrityError, match="ck_orders_not_own_listing"):
        make_order(
            db_session,
            reservation=make_reservation(db_session, listing=listing),
            buyer_id=listing.seller_id,
        )


@pytest.mark.parametrize("live", ["CREATED", "PENDING", "SUCCEEDED"])
def test_an_order_has_one_live_payment(db_session, live):
    order = make_order(db_session)
    make_payment(db_session, order=order, status="FAILED")
    make_payment(db_session, order=order, status=live)
    with pytest.raises(IntegrityError, match="uq_payments_one_live_per_order"):
        make_payment(db_session, order=order, status="CREATED")


@pytest.mark.parametrize(
    "fields, rule",
    [
        ({"status": "MAYBE"}, "ck_payments_status"),
        ({"amount_paise": -1}, "ck_payments_amount"),
        ({"currency": "USD"}, "ck_payments_currency"),
        ({"provider": "realpay"}, "ck_payments_provider"),
    ],
)
def test_what_a_payment_cannot_be(db_session, fields, rule):
    with pytest.raises(IntegrityError, match=rule):
        make_payment(db_session, **fields)


def test_one_transaction_is_one_payment(db_session):
    make_payment(db_session, provider_transaction_id="txn_1")
    with pytest.raises(IntegrityError, match="uq_payments_provider_transaction"):
        make_payment(db_session, provider_transaction_id="txn_1")


def test_an_event_is_stored_once(db_session):
    make_payment_event(db_session, provider_event_id="evt_1")
    with pytest.raises(IntegrityError, match="uq_payment_events_provider_event"):
        make_payment_event(db_session, provider_event_id="evt_1")
