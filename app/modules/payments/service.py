"""Payment logic (FR-09). Functions take a Session and never commit; the one route that must
commit halfway (router.create_intent) says so where it does.

Settling a payment changes four rows: the payment, its order, its reservation and, when it
failed, the listing. `succeed()` and `fail()` are the only functions that do it, whoever found
out: a webhook, reconciliation or the expiry job. Both lock the reservation first
(orders/service.py has the lock order), then look at the payment, so however many of them run
at once, one settles it and the rest find it settled.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.infrastructure.payments import GatewayIntent
from app.modules.audit.service import record
from app.modules.identity.models import User, UserRole
from app.modules.listings.service import release_listing
from app.modules.orders.models import Order, OrderStatus
from app.modules.orders.service import check_payable, get_order_for, lock_reservation
from app.modules.orders.state import transition as move_order
from app.modules.outbox.service import EventType, emit
from app.modules.payments.errors import OrderNotPayableError, PaymentNotFoundError
from app.modules.payments.models import OPEN_PAYMENT_STATUSES, Payment, PaymentStatus
from app.modules.payments.state import transition
from app.modules.reservations.errors import BuyerOnlyError
from app.modules.reservations.models import Reservation, ReservationStatus
from app.modules.reservations.state import transition as move_reservation

log = structlog.get_logger(__name__)


# ------------------------------------------------------------------ starting a payment


def start_payment(db: Session, order_id: uuid.UUID, user: User) -> tuple[Payment, bool]:
    """Take the reservation out of the clock's reach and save the payment, before anyone talks
    to the gateway. Returns the payment, and whether it is new: an order whose payment is still
    open gets that payment back, so a retry after a crash, a timeout or a closed tab continues
    the one payment instead of starting another."""
    order = get_order_for(db, order_id, user)
    if order.buyer_id != user.id:
        raise BuyerOnlyError()
    reservation = lock_reservation(db, order.reservation_id)
    db.refresh(order)

    if order.status == OrderStatus.PAYMENT_PENDING:
        payment = _open_payment(db, order.id)
        if payment.provider_transaction_id is None:
            # This request may be about to create the intent that reconciliation, asking now,
            # would not find. Reconciliation leaves recently touched payments alone. A payment
            # with a transaction id is not touched: coming back to it must not hide it.
            db.execute(
                update(Payment).where(Payment.id == payment.id).values(updated_at=func.now())
            )
            db.refresh(payment)
        return payment, False
    if order.status != OrderStatus.CREATED:
        raise OrderNotPayableError(f"The order is {order.status}.", current_status=order.status)

    check_payable(db, reservation)
    move_reservation(db, reservation, ReservationStatus.PAYMENT_PENDING, actor_id=user.id)
    move_order(db, order, OrderStatus.PAYMENT_PENDING, actor_id=user.id)
    payment_id = uuid.uuid4()
    payment = Payment(
        id=payment_id,
        order_id=order.id,
        amount_paise=order.amount_paise,
        currency=order.currency,
        idempotency_key=str(payment_id),
    )
    db.add(payment)
    db.flush()
    db.refresh(payment)
    record(
        db,
        actor_id=user.id,
        entity_type="payment",
        entity_id=payment.id,
        action="payment.created",
        details={"order_id": order.id, "amount_paise": payment.amount_paise},
    )
    return payment, True


def _open_payment(db: Session, order_id: uuid.UUID) -> Payment:
    return db.scalars(
        select(Payment).where(
            Payment.order_id == order_id, Payment.status.in_(OPEN_PAYMENT_STATUSES)
        )
    ).one()


class IntentMismatch(Exception):
    """The gateway answered with an intent that is not this payment's: another transaction,
    amount or currency. Nobody may be sent to pay it."""


def take_intent(db: Session, payment_id: uuid.UUID, intent: GatewayIntent) -> Payment:
    """attach_intent() for the buyer's request, which is about to send them to the intent's
    page: raises IntentMismatch rather than send anyone to pay what we would not recognise."""
    payment, _, _ = _locked(db, payment_id)
    if payment.status in OPEN_PAYMENT_STATUSES and (
        payment.provider_transaction_id not in (None, intent.transaction_id)
        or (intent.amount_paise, intent.currency) != (payment.amount_paise, payment.currency)
    ):
        log.error(
            "payment.intent_mismatch",
            payment_id=str(payment.id),
            ours=payment.provider_transaction_id,
            theirs=intent.transaction_id,
        )
        raise IntentMismatch()
    return attach_intent(db, payment_id, intent)


def attach_intent(db: Session, payment_id: uuid.UUID, intent: GatewayIntent) -> Payment:
    """The gateway has an intent for this payment: keep its transaction id. Harmless to repeat."""
    payment, _, _ = _locked(db, payment_id)
    if payment.status == PaymentStatus.CREATED:
        transition(
            db,
            payment,
            PaymentStatus.PENDING,
            values={
                "provider_transaction_id": intent.transaction_id,
                "provider_response": intent.snapshot,
            },
            details={"transaction_id": intent.transaction_id},
        )
    return payment


def refused(db: Session, payment_id: uuid.UUID, *, new: bool) -> bool:
    """The gateway would not make an intent for this payment. True if the payment was failed and
    the ticket is back on sale, which happens only to a new payment: nothing else can have made
    an intent for it. Otherwise nothing changes, since an intent an earlier try made may still
    be paid on its page; reconciliation will find out. Raises OrderNotPayableError if the
    payment was settled meanwhile."""
    if new and fail(db, payment_id, reason="refused", source="gateway", without_intent=True):
        return True
    check_open(locked_payment(db, payment_id))
    log.error("payment.refused_but_kept", payment_id=str(payment_id), new=new)
    return False


def check_open(payment: Payment) -> None:
    """Raise if the payment was settled while we were talking to the gateway: nobody may be
    sent to pay for it any more."""
    if payment.status not in OPEN_PAYMENT_STATUSES:
        raise OrderNotPayableError(
            f"The order is {payment.order.status}.", current_status=payment.order.status
        )


# ------------------------------------------------------------------ settling a payment


def _locked(db: Session, payment_id: uuid.UUID) -> tuple[Payment, Order, Reservation]:
    """The payment and what hangs on it, as they are now, held still by the reservation's lock."""
    reservation_id = db.scalar(
        select(Order.reservation_id)
        .join(Payment, Payment.order_id == Order.id)
        .where(Payment.id == payment_id)
    )
    if reservation_id is None:
        raise PaymentNotFoundError()
    reservation = lock_reservation(db, reservation_id)
    payment = db.scalars(
        select(Payment).where(Payment.id == payment_id).execution_options(populate_existing=True)
    ).one()
    return payment, payment.order, reservation


def succeed(
    db: Session, payment_id: uuid.UUID, *, source: str, snapshot: Mapping[str, Any]
) -> bool:
    """The money arrived. False if the payment is already settled; nothing changes then."""
    payment, order, reservation = _locked(db, payment_id)
    if payment.status == PaymentStatus.FAILED:
        # We told everyone this failed and may have given the ticket away. The gateway refuses
        # to take money for an expired or declined intent, so this should never happen, and
        # there are no refunds: a person has to look.
        log.error("payment.succeeded_after_failing", payment_id=str(payment.id), source=source)
    if payment.status not in OPEN_PAYMENT_STATUSES:
        return False
    details = {"source": source}
    transition(
        db,
        payment,
        PaymentStatus.SUCCEEDED,
        values={"provider_response": dict(snapshot)},
        details=details,
    )
    move_order(db, order, OrderStatus.PAID, actor_id=None, details=details)
    move_reservation(db, reservation, ReservationStatus.CONFIRMED, actor_id=None, details=details)
    # In this transaction: the ticket is handed over if, and only if, this commits.
    emit(
        db,
        aggregate_type="order",
        aggregate_id=order.id,
        event_type=EventType.PAYMENT_SUCCEEDED,
        payload={
            "order_id": order.id,
            "payment_id": payment.id,
            "reservation_id": reservation.id,
            "listing_id": order.listing_id,
            "buyer_id": order.buyer_id,
            "seller_id": order.seller_id,
            "amount_paise": order.amount_paise,
        },
    )
    return True


def fail(
    db: Session,
    payment_id: uuid.UUID,
    *,
    reason: str,
    source: str,
    snapshot: Mapping[str, Any] | None = None,
    timed_out: bool = False,
    untouched_since: datetime | None = None,
    once_hold_ended: bool = False,
    without_intent: bool = False,
) -> bool:
    """No money will arrive: the ticket goes back on sale. False if nothing was changed: it was
    settled already, or one of the conditions below did not hold.

    `timed_out`: nobody was refused, the buyer just never paid. The order is CANCELLED and the
    reservation EXPIRED, rather than failed. For callers whose reason may be out of date, all
    checked under the lock: `untouched_since`, only if nobody has touched the payment after
    that moment; `once_hold_ended`, only if the buyer's time to try again is over;
    `without_intent`, only if no transaction id was ever kept for it, since an intent that
    exists may still be paid on its page."""
    payment, order, reservation = _locked(db, payment_id)
    if payment.status not in OPEN_PAYMENT_STATUSES:
        return False
    if untouched_since is not None and payment.updated_at > untouched_since:
        return False
    if without_intent and payment.provider_transaction_id is not None:
        return False
    if once_hold_ended and reservation.expires_at > db.scalar(select(func.clock_timestamp())):
        return False
    details = {"source": source, "reason": reason}
    values: dict[str, Any] = {"failure_reason": reason}
    if snapshot is not None:
        values["provider_response"] = dict(snapshot)
    transition(db, payment, PaymentStatus.FAILED, values=values, details=details)
    move_order(
        db,
        order,
        OrderStatus.CANCELLED if timed_out else OrderStatus.PAYMENT_FAILED,
        actor_id=None,
        details=details,
    )
    move_reservation(
        db,
        reservation,
        ReservationStatus.EXPIRED if timed_out else ReservationStatus.FAILED,
        actor_id=None,
        details=details,
    )
    release_listing(
        db,
        reservation.listing_id,
        actor_id=None,
        reason="payment_timeout" if timed_out else "payment_failed",
    )
    emit(
        db,
        aggregate_type="order",
        aggregate_id=order.id,
        event_type=EventType.PAYMENT_FAILED,
        payload={
            "order_id": order.id,
            "payment_id": payment.id,
            "listing_id": order.listing_id,
            "buyer_id": order.buyer_id,
            "seller_id": order.seller_id,
            "reason": reason,
        },
    )
    return True


def locked_payment(db: Session, payment_id: uuid.UUID) -> Payment:
    """The payment as it is now, held still by its reservation's lock."""
    return _locked(db, payment_id)[0]


# ------------------------------------------------------------------ reading


def get_payment_for(db: Session, payment_id: uuid.UUID, user: User) -> Payment:
    """The order's buyer and admins. Not the seller: how someone paid is not their business."""
    payment = db.get(Payment, payment_id)
    if payment is None or not (user.id == payment.order.buyer_id or user.role == UserRole.ADMIN):
        raise PaymentNotFoundError()
    return payment
