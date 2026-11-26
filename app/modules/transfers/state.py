"""The transfer state machine. docs/state-machines.md has the same table with the reasons."""

from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.modules.audit.machine import StateMachine
from app.modules.transfers.errors import IllegalTransferTransitionError, TransferChangedError
from app.modules.transfers.models import Transfer, TransferStatus

S = TransferStatus

ALLOWED: dict[TransferStatus, frozenset[TransferStatus]] = {
    # Straight to review when there is nothing to ask the provider about (the ticket is gone).
    S.INITIATED: frozenset({S.PROCESSING, S.REQUIRES_MANUAL_REVIEW}),
    # PROCESSING to itself: another attempt.
    S.PROCESSING: frozenset({S.PROCESSING, S.COMPLETED, S.REQUIRES_MANUAL_REVIEW}),
    S.COMPLETED: frozenset(),
    S.REQUIRES_MANUAL_REVIEW: frozenset(),
}

MACHINE = StateMachine(
    Transfer,
    "transfer",
    TransferStatus,
    ALLOWED,
    illegal=IllegalTransferTransitionError,
    changed=TransferChangedError,
    named={
        (S.INITIATED, S.PROCESSING): "transfer.attempted",
        (S.PROCESSING, S.PROCESSING): "transfer.attempted",
        (S.INITIATED, S.REQUIRES_MANUAL_REVIEW): "transfer.needs_review",
        (S.PROCESSING, S.REQUIRES_MANUAL_REVIEW): "transfer.needs_review",
    },
)


def transition(
    db: Session,
    transfer: Transfer,
    to: TransferStatus,
    *,
    values: Mapping[str, Any] | None = None,
    details: Mapping[str, Any] | None = None,
) -> Transfer:
    """Every transfer status change; always the system's doing."""
    return MACHINE.move(db, transfer, to, actor_id=None, values=values, details=details)
