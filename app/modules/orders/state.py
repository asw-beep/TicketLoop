"""The order state machine. docs/state-machines.md has the same table with the reasons."""

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.machine import StateMachine
from app.modules.orders.errors import IllegalOrderTransitionError, OrderChangedError
from app.modules.orders.models import Order, OrderStatus

S = OrderStatus

ALLOWED: dict[OrderStatus, frozenset[OrderStatus]] = {
    S.CREATED: frozenset({S.PAYMENT_PENDING, S.CANCELLED}),
    S.PAYMENT_PENDING: frozenset({S.PAID, S.PAYMENT_FAILED, S.CANCELLED}),
    S.PAID: frozenset({S.TRANSFER_PENDING}),
    S.TRANSFER_PENDING: frozenset({S.COMPLETED, S.TRANSFER_FAILED}),
    S.PAYMENT_FAILED: frozenset(),
    S.CANCELLED: frozenset(),
    S.COMPLETED: frozenset(),
    S.TRANSFER_FAILED: frozenset(),
}

MACHINE = StateMachine(
    Order,
    "order",
    OrderStatus,
    ALLOWED,
    illegal=IllegalOrderTransitionError,
    changed=OrderChangedError,
)
ACTION = MACHINE.action


def transition(
    db: Session,
    order: Order,
    to: OrderStatus,
    *,
    actor_id: uuid.UUID | None,
    details: Mapping[str, Any] | None = None,
) -> Order:
    """Every order status change. See StateMachine.move()."""
    return MACHINE.move(db, order, to, actor_id=actor_id, details=details)


def cancel_unpaid_order(
    db: Session, reservation_id: uuid.UUID, *, actor_id: uuid.UUID | None, reason: str
) -> None:
    """Its hold ended before anyone started paying, so the order, if there is one, ends too.
    Called inside the reservation's own transition, whose row lock the caller holds."""
    order = db.scalar(
        select(Order)
        .where(Order.reservation_id == reservation_id, Order.status == S.CREATED)
        .execution_options(populate_existing=True)
    )
    if order is not None:
        transition(db, order, S.CANCELLED, actor_id=actor_id, details={"reason": reason})
