"""The payment state machine. docs/state-machines.md has the same table with the reasons."""

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.modules.audit.machine import StateMachine
from app.modules.payments.errors import IllegalPaymentTransitionError, PaymentChangedError
from app.modules.payments.models import Payment, PaymentStatus

S = PaymentStatus

ALLOWED: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    S.CREATED: frozenset({S.PENDING, S.FAILED}),
    S.PENDING: frozenset({S.SUCCEEDED, S.FAILED}),
    S.SUCCEEDED: frozenset(),
    S.FAILED: frozenset(),
}

MACHINE = StateMachine(
    Payment,
    "payment",
    PaymentStatus,
    ALLOWED,
    illegal=IllegalPaymentTransitionError,
    changed=PaymentChangedError,
)
ACTION = MACHINE.action


def transition(
    db: Session,
    payment: Payment,
    to: PaymentStatus,
    *,
    actor_id: uuid.UUID | None = None,
    values: Mapping[str, Any] | None = None,
    details: Mapping[str, Any] | None = None,
) -> Payment:
    """Every payment status change. See StateMachine.move()."""
    return MACHINE.move(db, payment, to, actor_id=actor_id, values=values, details=details)
