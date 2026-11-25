"""Reservation logic (FR-07). Functions take the request's Session and never commit.

The race is decided by one UPDATE on the listing row (claim_listing). Everything else here
happens only after that UPDATE matched, in the same transaction.

Lock order: the reservation row before its listing, everywhere except reserving, where the
reservation does not exist yet; and several listings in id order, as the event's changes take
them (listings/service.py). Transactions that take their locks in one order cannot deadlock.
"""

import uuid
from datetime import datetime

import structlog
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.core.pagination import Sort, paginate
from app.infrastructure.database.errors import violated_constraint
from app.modules.audit.service import record
from app.modules.identity.models import User, UserRole
from app.modules.listings.errors import (
    IllegalListingTransitionError,
    ListingChangedError,
    ListingNotFoundError,
)
from app.modules.listings.models import Listing, ListingStatus
from app.modules.listings.service import release_listing
from app.modules.listings.state import transition as move_listing
from app.modules.reservations.errors import (
    BuyerOnlyError,
    ListingNotAvailableError,
    OwnListingReservationError,
    ReservationNotCancellableError,
    ReservationNotFoundError,
)
from app.modules.reservations.models import (
    HOLD,
    LIVE_STATUSES,
    UNRESOLVED_STATUSES,
    Reservation,
    ReservationStatus,
)
from app.modules.reservations.state import transition

log = structlog.get_logger(__name__)
S = ReservationStatus

# Holds that still need something from their buyer come first, then newest first.
_UNRESOLVED = case((Reservation.status.in_(UNRESOLVED_STATUSES), 1), else_=0)
LIVE_FIRST = Sort(
    "reservations.live-first",
    (_UNRESOLVED, Reservation.created_at, Reservation.id),
    descending=True,
)
EXPIRY_BATCH = 100


def claim_listing(
    db: Session, listing: Listing, buyer_id: uuid.UUID, reservation_id: uuid.UUID
) -> None:
    """Move the listing from ACTIVE to RESERVED for this buyer, or raise.

    This one UPDATE decides the race. A hundred buyers may all have read ACTIVE; Postgres runs
    their UPDATEs on this row one at a time, and after the first commits the others find
    `status = 'ACTIVE'` no longer true and update nothing. The other two conditions are checked
    at that same instant, so a listing cannot be sold past its end or to its own seller.
    """
    move_listing(
        db,
        listing,
        ListingStatus.RESERVED,
        actor_id=buyer_id,
        details={"reservation_id": reservation_id},
        where=(Listing.seller_id != buyer_id, Listing.expires_at > func.now()),
    )


def reserve(db: Session, listing_id: uuid.UUID, buyer: User) -> tuple[Reservation, bool]:
    """Hold the listing for 10 minutes. Returns the reservation, and whether it is new: the
    buyer who already holds it gets the hold they have, so a retry or a double click is safe."""
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise ListingNotFoundError()
    if listing.seller_id == buyer.id:
        raise OwnListingReservationError()

    reservation_id = uuid.uuid4()  # chosen here so the listing's audit row can name it
    try:
        claim_listing(db, listing, buyer.id, reservation_id)
    except (IllegalListingTransitionError, ListingChangedError):
        held = _live_reservation(db, listing_id, buyer.id)
        if held is not None and not _ran_out(db, held):
            return held, False
        raise _not_available(listing, held) from None

    reservation = Reservation(
        id=reservation_id,
        listing_id=listing_id,
        buyer_id=buyer.id,
        expires_at=func.now() + HOLD,
    )
    try:
        with db.begin_nested():  # a SAVEPOINT, so the request can still answer
            db.add(reservation)
            db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == "uq_reservations_one_live_per_listing":
            # The safety net. Raising rolls the whole request back, the claim included.
            raise ListingNotAvailableError("The listing is reserved.", reason="reserved") from None
        raise
    db.refresh(reservation)
    record(
        db,
        actor_id=buyer.id,
        entity_type="reservation",
        entity_id=reservation.id,
        action="reservation.created",
        details={"listing_id": listing_id, "expires_at": reservation.expires_at},
    )
    return reservation, True


def _live_reservation(
    db: Session, listing_id: uuid.UUID, buyer_id: uuid.UUID
) -> Reservation | None:
    return db.scalar(
        select(Reservation).where(
            Reservation.listing_id == listing_id,
            Reservation.buyer_id == buyer_id,
            Reservation.status.in_(LIVE_STATUSES),
        )
    )


def _ran_out(db: Session, reservation: Reservation) -> bool:
    """An unpaid hold past its end is over, even if the expiry loop has not reached it yet."""
    return reservation.status == S.CREATED and reservation.expires_at <= db.scalar(
        select(func.now())
    )


def _not_available(listing: Listing, held: Reservation | None) -> AppError:
    """Why the listing cannot be reserved. One that was never on sale to this buyer is the same
    404 as an ID that does not exist."""
    if held is not None:
        return ListingNotAvailableError(
            "Your hold ran out a moment ago.",
            reason="hold-ended",
        )
    match ListingStatus(listing.status):
        case ListingStatus.RESERVED:
            return ListingNotAvailableError("Another buyer is holding it.", reason="reserved")
        case ListingStatus.SOLD:
            return ListingNotAvailableError("It has been sold.", reason="sold")
        case ListingStatus.ACTIVE | ListingStatus.EXPIRED:
            # ACTIVE: its end has passed and the expiry job has not reached it yet.
            return ListingNotAvailableError("The listing has ended.", reason="expired")
        case _:
            return ListingNotFoundError()


def get_reservation_for(db: Session, reservation_id: uuid.UUID, user: User) -> Reservation:
    """The buyer, the listing's seller and admins may see a reservation. To anyone else it is
    the same 404 as one that does not exist."""
    reservation = db.get(Reservation, reservation_id)
    if reservation is None or not (
        user.id in (reservation.buyer_id, reservation.listing.seller_id)
        or user.role == UserRole.ADMIN
    ):
        raise ReservationNotFoundError()
    return reservation


def list_my_reservations(
    db: Session, buyer: User, cursor: str | None, limit: int
) -> tuple[list[Reservation], str | None]:
    stmt = select(Reservation).where(Reservation.buyer_id == buyer.id)
    return paginate(db, stmt, LIVE_FIRST, cursor, limit)


def cancel_reservation(db: Session, reservation_id: uuid.UUID, user: User) -> Reservation:
    """The buyer gives up an unpaid hold; the ticket goes back on sale."""
    reservation = get_reservation_for(db, reservation_id, user)
    if reservation.buyer_id != user.id:
        raise BuyerOnlyError()
    if reservation.status != S.CREATED:
        raise ReservationNotCancellableError(f"The reservation is {reservation.status}.")
    transition(db, reservation, S.CANCELLED, actor_id=user.id)
    release_listing(db, reservation.listing_id, actor_id=user.id, reason="buyer_cancelled")
    return reservation


def expire_reservations(
    db: Session, now: datetime | None = None, limit: int = EXPIRY_BATCH
) -> tuple[int, int]:
    """Expire unpaid holds past their end and put their tickets back on sale, as the system.
    Returns (found, expired).

    Only CREATED ones: PAYMENT_PENDING means money may be moving, and the clock alone must not
    decide that (hard problem 5). SKIP LOCKED: a hold someone is cancelling or paying for right
    now is left to them, and two of these running together never wait on each other.
    """
    # The database's clock, which wrote expires_at and is what reserve() goes by.
    now = now or db.scalar(select(func.now()))
    due = db.scalars(
        select(Reservation)
        .where(Reservation.status == S.CREATED, Reservation.expires_at <= now)
        .order_by(Reservation.expires_at, Reservation.id)
        .limit(limit)
        .with_for_update(of=Reservation, skip_locked=True)
        .execution_options(populate_existing=True)
    ).all()
    expired = 0
    # Each release locks a listing until the commit: in id order, or an event being cancelled
    # or moved at this moment would deadlock with us.
    for reservation in sorted(due, key=lambda r: r.listing_id):
        try:
            # A SAVEPOINT each: one row in a state it should not be in must not stop the rest
            # from expiring, now and on every later run.
            with db.begin_nested():
                transition(db, reservation, S.EXPIRED, actor_id=None)
                release_listing(
                    db, reservation.listing_id, actor_id=None, reason="hold_expired", now=now
                )
            expired += 1
        except AppError as error:
            log.error(
                "reservation.expiry_failed", reservation_id=str(reservation.id), error=str(error)
            )
    return len(due), expired
