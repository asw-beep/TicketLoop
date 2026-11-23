"""The listing state machine: which status moves exist, and the one function that makes them.

Every listing status change in the codebase goes through `transition()`. It checks the move
against `ALLOWED`, applies it with a conditional UPDATE, and writes the audit row in the same
transaction, so a status change and its audit row commit together or not at all.

`transition()` knows nothing about who may act. Ownership, roles and the ticket and event
checks live in the service functions that call it, and each of those also names the states it
acts from: `ALLOWED` describes the whole machine, including moves (RESERVED -> ACTIVE) that an
M5 route must never make.
"""

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.modules.audit.service import record
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

# The audit action for each allowed move. M5 reaches submitted, approved, rejected, cancelled
# and expired; the rest are named now so the table-driven test covers the whole machine, and
# the modules that reach them (M8, M12) may rename them.
_NAMED: dict[tuple[ListingStatus, ListingStatus], str] = {
    (S.DRAFT, S.PENDING_VERIFICATION): "listing.submitted",
    (S.PENDING_VERIFICATION, S.ACTIVE): "listing.approved",
    (S.PENDING_VERIFICATION, S.REJECTED): "listing.rejected",
    (S.ACTIVE, S.RESERVED): "listing.reserved",
    (S.RESERVED, S.ACTIVE): "listing.released",
    (S.RESERVED, S.SOLD): "listing.sold",
}
ACTION: dict[tuple[ListingStatus, ListingStatus], str] = {
    (from_, to): _NAMED.get((from_, to), f"listing.{to.lower()}")
    for from_, tos in ALLOWED.items()
    for to in tos
}


def transition(
    db: Session,
    listing: Listing,
    to: ListingStatus,
    *,
    actor_id: uuid.UUID | None,
    values: Mapping[str, Any] | None = None,
    details: Mapping[str, Any] | None = None,
) -> Listing:
    """Move `listing` to `to`, write its audit row, and return the refreshed listing.

    The status this function read is repeated in the UPDATE's WHERE clause. Checking it in
    Python and then writing an unconditional UPDATE would leave a gap between the check and the
    write where another request could change the listing; with the check inside the UPDATE,
    Postgres decides atomically, and zero rows updated means someone else got there first
    (`ListingChangedError`). The caller's transaction then rolls back, audit row and all.

    `values` are the other columns the move sets (reviewed_by, reviewed_at, rejection_reason).
    `details` are added to the audit row's {"from", "to"}.
    """
    seen = ListingStatus(listing.status)
    if to not in ALLOWED[seen]:
        raise IllegalListingTransitionError(
            f"A {seen} listing cannot become {to}.", current_status=seen
        )
    moved = db.execute(
        update(Listing)
        .where(Listing.id == listing.id, Listing.status == seen)
        .values(status=to, updated_at=func.now(), **(values or {}))
        .returning(Listing.id)
    ).scalar_one_or_none()
    if moved is None:
        db.refresh(listing)
        raise ListingChangedError(
            f"The listing is now {listing.status}.", current_status=listing.status
        )
    record(
        db,
        actor_id=actor_id,
        entity_type="listing",
        entity_id=listing.id,
        action=ACTION[(seen, to)],
        details={"from": seen, "to": to, **(details or {})},
    )
    db.refresh(listing)
    return listing
