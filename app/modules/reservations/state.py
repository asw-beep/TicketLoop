"""The reservation state machine. docs/state-machines.md has the same table with the reasons."""

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.modules.audit.machine import StateMachine
from app.modules.reservations.errors import (
    IllegalReservationTransitionError,
    ReservationChangedError,
)
from app.modules.reservations.models import Reservation, ReservationStatus

S = ReservationStatus

ALLOWED: dict[ReservationStatus, frozenset[ReservationStatus]] = {
    S.CREATED: frozenset({S.PAYMENT_PENDING, S.EXPIRED, S.CANCELLED}),
    S.PAYMENT_PENDING: frozenset({S.CONFIRMED, S.FAILED, S.EXPIRED}),
    S.CONFIRMED: frozenset(),
    S.EXPIRED: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
}

MACHINE = StateMachine(
    Reservation,
    "reservation",
    ReservationStatus,
    ALLOWED,
    illegal=IllegalReservationTransitionError,
    changed=ReservationChangedError,
)
ACTION = MACHINE.action


def transition(
    db: Session,
    reservation: Reservation,
    to: ReservationStatus,
    *,
    actor_id: uuid.UUID | None,
    details: Mapping[str, Any] | None = None,
) -> Reservation:
    """Every reservation status change. See StateMachine.move()."""
    return MACHINE.move(db, reservation, to, actor_id=actor_id, details=details)
