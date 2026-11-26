"""Order logic (FR-08). Functions take the request's Session and never commit.

Lock order, here and in payments: the reservation row first, then its order, its payment and
its listing. The reservation is what paying, cancelling and the clock fight over, so whoever
holds its lock can read the rest and trust it until they commit.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.pagination import Sort, paginate
from app.infrastructure.database.errors import violated_constraint
from app.modules.audit.service import record
from app.modules.identity.models import User, UserRole
from app.modules.orders.errors import (
    OrderExistsError,
    OrderNotFoundError,
    ReservationNotPayableError,
)
from app.modules.orders.models import Order, OrderStatus
from app.modules.orders.schemas import OrderRole
from app.modules.reservations.errors import ReservationNotFoundError
from app.modules.reservations.models import LIVE_STATUSES, Reservation, ReservationStatus

NEWEST = Sort("orders.newest", (Order.created_at, Order.id), descending=True)


def lock_reservation(db: Session, reservation_id: uuid.UUID) -> Reservation | None:
    """The reservation, as it is now, and nobody else's to change until this transaction ends."""
    return db.scalar(
        select(Reservation)
        .where(Reservation.id == reservation_id)
        .with_for_update(of=Reservation)
        .execution_options(populate_existing=True)
    )


def check_payable(db: Session, reservation: Reservation) -> None:
    """Raise unless this locked hold is unpaid and has time left. clock_timestamp(), because
    waiting for the lock takes time that now(), the transaction's start, does not see."""
    if reservation.status != ReservationStatus.CREATED:
        raise ReservationNotPayableError(
            f"The reservation is {reservation.status}.", reservation_status=reservation.status
        )
    if reservation.expires_at <= db.scalar(select(func.clock_timestamp())):
        raise ReservationNotPayableError(
            "The hold ran out.", reservation_status=reservation.status, reason="hold-ended"
        )


def create_order(
    db: Session, reservation_id: uuid.UUID, buyer: User, idempotency_key: str
) -> Order:
    reservation = lock_reservation(db, reservation_id)
    if reservation is None or reservation.buyer_id != buyer.id:
        raise ReservationNotFoundError()
    existing = _order_of(db, reservation.id)
    if existing is not None and reservation.status in LIVE_STATUSES:
        raise OrderExistsError("Continue with the order you have.", order_id=str(existing))
    check_payable(db, reservation)

    listing = reservation.listing
    order = Order(
        reservation_id=reservation.id,
        listing_id=listing.id,
        buyer_id=buyer.id,
        seller_id=listing.seller_id,
        amount_paise=listing.asking_price_paise,
        idempotency_key=idempotency_key,
    )
    try:
        with db.begin_nested():
            db.add(order)
            db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == "uq_orders_reservation":
            # The safety net. The reservation's lock means no code path gets here.
            raise OrderExistsError(
                "Continue with the order you have.", order_id=str(_order_of(db, reservation.id))
            ) from None
        raise
    db.refresh(order)
    record(
        db,
        actor_id=buyer.id,
        entity_type="order",
        entity_id=order.id,
        action="order.created",
        details={
            "reservation_id": reservation.id,
            "listing_id": listing.id,
            "amount_paise": order.amount_paise,
        },
    )
    return order


def _order_of(db: Session, reservation_id: uuid.UUID) -> uuid.UUID | None:
    return db.scalar(select(Order.id).where(Order.reservation_id == reservation_id))


def get_order_for(db: Session, order_id: uuid.UUID, user: User) -> Order:
    """The buyer, the seller and admins may see an order. To anyone else it is the same 404 as
    one that does not exist."""
    order = db.get(Order, order_id)
    if order is None or not (
        user.id in (order.buyer_id, order.seller_id) or user.role == UserRole.ADMIN
    ):
        raise OrderNotFoundError()
    return order


def list_my_orders(
    db: Session,
    user: User,
    role: OrderRole,
    status: OrderStatus | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[Order], str | None]:
    """What the caller bought, or sold, newest first."""
    mine = Order.buyer_id if role == "buyer" else Order.seller_id
    stmt = select(Order).where(mine == user.id)
    if status is not None:
        stmt = stmt.where(Order.status == status)
    return paginate(db, stmt, NEWEST, cursor, limit)
