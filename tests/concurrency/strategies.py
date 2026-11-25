"""Two other ways to let exactly one buyer win, for measurement only. The app uses neither.

docs/reservation-design.md compares them with the conditional UPDATE the app runs.
"""

import uuid
from contextvars import ContextVar

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.identity.models import User
from app.modules.listings.errors import ListingChangedError
from app.modules.listings.models import Listing, ListingStatus
from app.modules.listings.state import transition as move_listing
from app.modules.reservations import service
from app.modules.reservations.models import Reservation

# Counted in place by the request's thread and read by strategy_app's middleware: a ContextVar
# is copied into the thread, the list inside it is the same object.
attempts: ContextVar[list[int]] = ContextVar("attempts")
MAX_ATTEMPTS = 20
app_reserve = service.reserve  # before strategy_app replaces it


def _check_and_move(
    db: Session, listing: Listing, buyer_id: uuid.UUID, reservation_id: uuid.UUID
) -> None:
    """Read, decide in Python, then write: correct only if nothing can change in between."""
    ended = listing.expires_at <= db.scalar(select(func.now()))
    if listing.status != ListingStatus.ACTIVE or ended or listing.seller_id == buyer_id:
        raise ListingChangedError("Not available.", current_status=listing.status)
    move_listing(
        db,
        listing,
        ListingStatus.RESERVED,
        actor_id=buyer_id,
        details={"reservation_id": reservation_id},
    )


def claim_with_row_lock(
    db: Session, listing: Listing, buyer_id: uuid.UUID, reservation_id: uuid.UUID
) -> None:
    """Pessimistic: SELECT ... FOR UPDATE first. Nobody else can change the row until we commit,
    so the check in Python stays true. Every buyer queues for the lock, losers included."""
    db.refresh(listing, with_for_update={"of": Listing})
    _check_and_move(db, listing, buyer_id, reservation_id)


def reserve_serializable(
    db: Session, listing_id: uuid.UUID, buyer: User
) -> tuple[Reservation, bool]:
    """Optimistic: no lock and no condition in the UPDATE. SERIALIZABLE makes Postgres abort any
    transaction whose reads another one invalidated (SQLSTATE 40001); we run it again."""
    buyer_id = buyer.id
    while True:
        attempts.get()[0] += 1
        db.rollback()  # end the transaction the token check opened, or the last failed attempt
        db.connection(execution_options={"isolation_level": "SERIALIZABLE"})
        try:
            found = app_reserve(db, listing_id, db.get_one(User, buyer_id))
            db.commit()  # the conflict may only be found here
            return found
        except DBAPIError as error:
            conflict = getattr(error.orig, "sqlstate", None) == "40001"
            if not conflict or attempts.get()[0] >= MAX_ATTEMPTS:
                raise


def install(strategy: str) -> None:
    """Swap the strategy into the app. Called in each worker process by strategy_app."""
    match strategy:
        case "conditional_update":
            pass
        case "row_lock":
            service.claim_listing = claim_with_row_lock
        case "serializable":
            service.claim_listing = _check_and_move
            service.reserve = reserve_serializable
        case _:
            raise ValueError(f"unknown strategy {strategy!r}")
