"""The listing state machine: which status moves exist, and the one function that makes them.

Every listing status change in the codebase goes through `transition()`, which is
`StateMachine.move()` with this table (app/modules/audit/machine.py).
"""

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import ColumnElement
from sqlalchemy.orm import Session

from app.modules.audit.machine import StateMachine
from app.modules.listings.errors import IllegalListingTransitionError, ListingChangedError
from app.modules.listings.models import Listing, ListingStatus

S = ListingStatus

# docs/state-machines.md. tests/integration/test_listing_transitions.py holds a copy.
ALLOWED: dict[ListingStatus, frozenset[ListingStatus]] = {
    S.DRAFT: frozenset({S.PENDING_VERIFICATION, S.CANCELLED}),
    S.PENDING_VERIFICATION: frozenset({S.ACTIVE, S.REJECTED, S.CANCELLED}),
    S.ACTIVE: frozenset({S.RESERVED, S.CANCELLED, S.EXPIRED}),
    S.RESERVED: frozenset({S.ACTIVE, S.EXPIRED, S.SOLD, S.CANCELLED}),
    S.SOLD: frozenset(),
    S.REJECTED: frozenset(),
    S.CANCELLED: frozenset(),
    S.EXPIRED: frozenset(),
}

MACHINE = StateMachine(
    Listing,
    "listing",
    ListingStatus,
    ALLOWED,
    illegal=IllegalListingTransitionError,
    changed=ListingChangedError,
    named={
        (S.DRAFT, S.PENDING_VERIFICATION): "listing.submitted",
        (S.PENDING_VERIFICATION, S.ACTIVE): "listing.approved",
        (S.PENDING_VERIFICATION, S.REJECTED): "listing.rejected",
        (S.ACTIVE, S.RESERVED): "listing.reserved",
        (S.RESERVED, S.ACTIVE): "listing.released",
        (S.RESERVED, S.SOLD): "listing.sold",
    },
)
ACTION = MACHINE.action


def transition(
    db: Session,
    listing: Listing,
    to: ListingStatus,
    *,
    actor_id: uuid.UUID | None,
    values: Mapping[str, Any] | None = None,
    details: Mapping[str, Any] | None = None,
    where: Sequence[ColumnElement[bool]] = (),
) -> Listing:
    """Every listing status change. See StateMachine.move()."""
    return MACHINE.move(
        db, listing, to, actor_id=actor_id, values=values, details=details, where=where
    )
