"""Listing logic (FR-04). Functions take the request's Session and never commit.

Who may see a listing is decided in one place, get_listing_for(). Every route that takes a
listing ID goes through it: the seller and admins see any status, everyone else sees only
ACTIVE listings, and every other case is the same 404 as an ID that does not exist.

Every status change goes through state.transition(). The only listing audit rows written here
directly are for changes that are not status moves: created, price_changed, expiry_changed.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.pagination import Sort, paginate
from app.infrastructure.database.errors import violated_constraint
from app.modules.audit.models import AuditLog
from app.modules.audit.service import record
from app.modules.events.models import EventStatus
from app.modules.identity.models import User, UserRole
from app.modules.listings.errors import (
    BadExpiryError,
    EventHasReservationsError,
    ListingAlreadyOpenError,
    ListingChangedError,
    ListingNotApprovableError,
    ListingNotCancellableError,
    ListingNotDraftError,
    ListingNotEditableError,
    ListingNotFoundError,
    ListingNotPendingError,
    OwnListingReviewError,
    SellerOnlyError,
    TicketNotListableError,
)
from app.modules.listings.models import OPEN_LISTING_STATUSES, Listing, ListingStatus
from app.modules.listings.rules import check_expiry, check_price
from app.modules.listings.schemas import HistoryEntryOut, ListingIn, ListingPatch
from app.modules.listings.state import transition
from app.modules.tickets.errors import EventNotOpenError, EventStartedError, OwnerOnlyError
from app.modules.tickets.models import Ticket, TicketStatus, VerificationStatus
from app.modules.tickets.service import get_visible_ticket

S = ListingStatus
EDITABLE = frozenset({S.DRAFT, S.PENDING_VERIFICATION, S.ACTIVE})
SELLER_CANCELLABLE = frozenset({S.DRAFT, S.PENDING_VERIFICATION, S.ACTIVE})
# A pending listing is rejected, not cancelled, by an admin.
ADMIN_CANCELLABLE = frozenset({S.ACTIVE})
# The only audit detail keys a listing's history shows. Everything else stays in the log.
SAFE_HISTORY_KEYS = frozenset(
    {
        "reason",
        "old_price_paise",
        "new_price_paise",
        "old_expires_at",
        "new_expires_at",
        "asking_price_paise",
        "expires_at",
    }
)

# The listing moves a buyer makes by taking, or giving up, a hold.
BUYER_ACTIONS = frozenset({"listing.reserved", "listing.released", "listing.expired"})

NEWEST_FIRST = Sort("listings.newest", (Listing.created_at, Listing.id), descending=True)
OLDEST_FIRST = Sort("listings.oldest", (Listing.created_at, Listing.id))
HISTORY_ORDER = Sort("audit.oldest", (AuditLog.created_at, AuditLog.id))


def can_see_everything(listing: Listing, user: User | None) -> bool:
    """The seller and admins see a listing in any status, in the full shape."""
    return user is not None and (listing.seller_id == user.id or user.role == UserRole.ADMIN)


def get_listing_for(db: Session, listing_id: uuid.UUID, user: User | None) -> Listing:
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise ListingNotFoundError()
    if listing.status != ListingStatus.ACTIVE and not can_see_everything(listing, user):
        raise ListingNotFoundError()  # not on sale: indistinguishable from not there
    return listing


def check_listable(ticket: Ticket, now: datetime) -> None:
    """Can this ticket be offered for sale right now? Checked on create, and again on submit
    and approve, because the ticket or its event may have changed since."""
    if ticket.verification_status != VerificationStatus.VERIFIED:
        raise TicketNotListableError(f"The ticket is {ticket.verification_status}, not VERIFIED.")
    if not ticket.transfer_eligible:
        raise TicketNotListableError("The ticket is not transfer-eligible.")
    if ticket.status != TicketStatus.ACTIVE:
        raise TicketNotListableError(f"The ticket is {ticket.status}.")
    event = ticket.event
    if event.status != EventStatus.PUBLISHED:
        raise EventNotOpenError(f"The event is {event.status}.")
    if event.starts_at <= now:
        raise EventStartedError()


def create_listing(db: Session, seller: User, data: ListingIn) -> Listing:
    ticket = get_visible_ticket(db, data.ticket_id, seller)
    if ticket.current_owner_id != seller.id:
        raise OwnerOnlyError("Only the ticket's owner can list it.")
    now = datetime.now(UTC)
    check_listable(ticket, now)
    check_price(data.asking_price_paise, ticket.original_price_paise)
    expires_at = data.expires_at or ticket.event.starts_at
    check_expiry(expires_at, ticket.event.starts_at, now)

    listing = Listing(
        ticket_id=ticket.id,
        event_id=ticket.event_id,
        seller_id=seller.id,
        asking_price_paise=data.asking_price_paise,
        original_price_paise=ticket.original_price_paise,
        expires_at=expires_at,
    )
    try:
        # The partial unique index decides "already listed", not a check-then-insert: two
        # requests listing the same ticket at the same instant cannot both succeed. The row is
        # added inside the SAVEPOINT (begin_nested flushes anything already pending before it
        # starts), so a duplicate rolls back only the savepoint.
        with db.begin_nested():
            db.add(listing)
            db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == "uq_listings_one_open_per_ticket":
            raise ListingAlreadyOpenError() from None
        raise
    record(
        db,
        actor_id=seller.id,
        entity_type="listing",
        entity_id=listing.id,
        action="listing.created",
        details={"asking_price_paise": listing.asking_price_paise, "expires_at": expires_at},
    )
    db.refresh(listing)
    return listing


def list_my_listings(
    db: Session,
    seller: User,
    status: ListingStatus | None,
    ticket_id: uuid.UUID | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[Listing], str | None]:
    stmt = select(Listing).where(Listing.seller_id == seller.id)
    if status is not None:
        stmt = stmt.where(Listing.status == status)
    if ticket_id is not None:
        stmt = stmt.where(Listing.ticket_id == ticket_id)
    return paginate(db, stmt, NEWEST_FIRST, cursor, limit)


def require_seller(listing: Listing, user: User) -> None:
    """For the seller's own actions. Call after get_listing_for(). An admin can already see
    the listing, so they get 403; anyone else gets the same 404 as for one they cannot see."""
    if listing.seller_id == user.id:
        return
    if user.role == UserRole.ADMIN:
        raise SellerOnlyError()
    raise ListingNotFoundError()


def update_listing(db: Session, listing_id: uuid.UUID, user: User, data: ListingPatch) -> Listing:
    listing = get_listing_for(db, listing_id, user)
    require_seller(listing, user)
    if listing.status not in EDITABLE:
        raise ListingNotEditableError(f"The listing is {listing.status}.")
    seen_status = ListingStatus(listing.status)
    seen_price, seen_expiry = listing.asking_price_paise, listing.expires_at

    changes: dict[str, object] = {}
    if data.asking_price_paise is not None:
        check_price(
            data.asking_price_paise,
            listing.original_price_paise,
            current=seen_price,
            status=seen_status,
        )
        if data.asking_price_paise != seen_price:
            changes["asking_price_paise"] = data.asking_price_paise
    if data.expires_at is not None:
        check_expiry(data.expires_at, listing.event.starts_at, datetime.now(UTC))
        if data.expires_at != seen_expiry:
            changes["expires_at"] = data.expires_at
    if not changes:
        return listing

    # Pin everything this edit was checked against. Otherwise an edit could land on a listing
    # an admin rejected a moment ago, or two price cuts racing on an ACTIVE listing could end
    # with the higher one written last.
    updated = db.execute(
        update(Listing)
        .where(
            Listing.id == listing.id,
            Listing.status == seen_status,
            Listing.asking_price_paise == seen_price,
            Listing.expires_at == seen_expiry,
        )
        .values(**changes, updated_at=func.now())
        .returning(Listing.id)
    ).scalar_one_or_none()
    if updated is None:
        db.refresh(listing)
        raise ListingChangedError(
            f"The listing is now {listing.status}.", current_status=listing.status
        )
    if "asking_price_paise" in changes:
        details = {"old_price_paise": seen_price, "new_price_paise": changes["asking_price_paise"]}
        _record_edit(db, user, listing, "listing.price_changed", details)
    if "expires_at" in changes:
        details = {"old_expires_at": seen_expiry, "new_expires_at": changes["expires_at"]}
        _record_edit(db, user, listing, "listing.expiry_changed", details)
    db.refresh(listing)
    return listing


def _record_edit(
    db: Session, user: User, listing: Listing, action: str, details: dict[str, object]
) -> None:
    record(
        db,
        actor_id=user.id,
        entity_type="listing",
        entity_id=listing.id,
        action=action,
        details=details,
    )


def submit_listing(db: Session, listing_id: uuid.UUID, user: User) -> Listing:
    """DRAFT to PENDING_VERIFICATION. The ticket and event are checked again: either may have
    changed since the draft was made."""
    listing = get_listing_for(db, listing_id, user)
    require_seller(listing, user)
    if listing.status != ListingStatus.DRAFT:
        raise ListingNotDraftError(f"The listing is {listing.status}.")
    now = datetime.now(UTC)
    check_listable(listing.ticket, now)
    if listing.expires_at <= now:
        raise BadExpiryError("The listing's expiry has passed. Change it, then submit.")
    return transition(db, listing, ListingStatus.PENDING_VERIFICATION, actor_id=user.id)


def cancel_listing(db: Session, listing_id: uuid.UUID, user: User) -> Listing:
    """The seller cancels while the listing is open and not reserved. An admin cancels only
    an ACTIVE listing; a pending one is rejected instead. On their own listing an admin is
    the seller."""
    listing = get_listing_for(db, listing_id, user)
    if listing.seller_id == user.id:
        allowed, by = SELLER_CANCELLABLE, "seller"
    elif user.role == UserRole.ADMIN:
        allowed, by = ADMIN_CANCELLABLE, "admin"
    else:
        raise ListingNotFoundError()  # they may see an ACTIVE listing, but it is not theirs
    if listing.status not in allowed:
        raise ListingNotCancellableError(f"The listing is {listing.status}.")
    return transition(db, listing, ListingStatus.CANCELLED, actor_id=user.id, details={"by": by})


def list_history(
    db: Session, listing_id: uuid.UUID, user: User, cursor: str | None, limit: int
) -> tuple[list[HistoryEntryOut], str | None]:
    """The listing's audit rows, oldest first, for its seller and admins."""
    listing = get_listing_for(db, listing_id, user)
    if not can_see_everything(listing, user):
        raise ListingNotFoundError()
    stmt = select(AuditLog).where(
        AuditLog.entity_type == "listing", AuditLog.entity_id == listing.id
    )
    rows, next_cursor = paginate(db, stmt, HISTORY_ORDER, cursor, limit)
    return [_history_entry(row, listing) for row in rows], next_cursor


def _history_entry(row: AuditLog, listing: Listing) -> HistoryEntryOut:
    """Who acted is shown as a role: the seller never learns which admin, or which buyer."""
    if row.actor_id is None:
        role = "system"
    elif row.actor_id == listing.seller_id:
        role = "seller"
    elif row.action in BUYER_ACTIONS:
        role = "buyer"
    else:
        role = "admin"
    to_status = ListingStatus.DRAFT if row.action == "listing.created" else row.details.get("to")
    return HistoryEntryOut(
        action=row.action,
        from_status=row.details.get("from"),
        to_status=to_status,
        actor_role=role,
        details={k: v for k, v in row.details.items() if k in SAFE_HISTORY_KEYS},
        created_at=row.created_at,
    )


def list_pending_listings(
    db: Session, cursor: str | None, limit: int
) -> tuple[list[Listing], str | None]:
    """The review queue, oldest first."""
    stmt = select(Listing).where(Listing.status == ListingStatus.PENDING_VERIFICATION)
    return paginate(db, stmt, OLDEST_FIRST, cursor, limit)


def list_all_listings(
    db: Session,
    status: ListingStatus | None,
    ticket_id: uuid.UUID | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[Listing], str | None]:
    """Every listing, newest first: how an admin reaches an ACTIVE listing to cancel it."""
    stmt = select(Listing)
    if status is not None:
        stmt = stmt.where(Listing.status == status)
    if ticket_id is not None:
        stmt = stmt.where(Listing.ticket_id == ticket_id)
    return paginate(db, stmt, NEWEST_FIRST, cursor, limit)


def approve_listing(db: Session, listing_id: uuid.UUID, admin: User) -> Listing:
    """PENDING_VERIFICATION to ACTIVE: buyers can see it from now on. The ticket, the event and
    the expiry are checked again; a listing that fails cannot be approved, only rejected."""
    listing = _listing_for_review(db, listing_id, admin)
    now = datetime.now(UTC)
    try:
        check_listable(listing.ticket, now)
        if listing.expires_at <= now:
            raise BadExpiryError("The listing's expiry has passed.")
    except (TicketNotListableError, EventNotOpenError, EventStartedError, BadExpiryError) as e:
        raise ListingNotApprovableError(e.detail or e.title) from None
    return transition(
        db,
        listing,
        ListingStatus.ACTIVE,
        actor_id=admin.id,
        values={"reviewed_by": admin.id, "reviewed_at": func.now()},
    )


def reject_listing(db: Session, listing_id: uuid.UUID, admin: User, reason: str) -> Listing:
    """PENDING_VERIFICATION to REJECTED, with a reason the seller sees."""
    listing = _listing_for_review(db, listing_id, admin)
    return transition(
        db,
        listing,
        ListingStatus.REJECTED,
        actor_id=admin.id,
        values={"reviewed_by": admin.id, "reviewed_at": func.now(), "rejection_reason": reason},
        details={"reason": reason},
    )


def _listing_for_review(db: Session, listing_id: uuid.UUID, admin: User) -> Listing:
    """Self-review first, then the state. Review acts only from PENDING_VERIFICATION, even
    though ALLOWED has RESERVED -> ACTIVE (a released reservation)."""
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise ListingNotFoundError()
    if listing.seller_id == admin.id:
        raise OwnListingReviewError()
    if listing.status != ListingStatus.PENDING_VERIFICATION:
        raise ListingNotPendingError(f"The listing is {listing.status}.")
    return listing


def cancel_open_listings_for_event(db: Session, event_id: uuid.UUID, actor_id: uuid.UUID) -> int:
    """Cancel the event's DRAFT, PENDING_VERIFICATION and ACTIVE listings, one transition and
    one audit row each, in the caller's transaction. Returns how many. Refused while any of
    them is RESERVED: a hold ends within 10 minutes unless a payment is under way, and a
    payment must not be cancelled underneath the buyer.

    The rows are locked first, RESERVED ones too. Locking only the ones to cancel would miss a
    listing being reserved at this moment: our SELECT would wait for that transaction, find the
    row no longer matches, and skip it.
    """
    listings = _lock_open_listings(db, event_id)
    reserved = sum(listing.status == S.RESERVED for listing in listings)
    if reserved:
        raise EventHasReservationsError(
            f"Tickets held by buyers: {reserved}. A hold ends within 10 minutes unless it is "
            "being paid for. Try again then.",
            reserved_listings=reserved,
        )
    for listing in listings:
        transition(
            db,
            listing,
            ListingStatus.CANCELLED,
            actor_id=actor_id,
            details={"reason": "event_cancelled"},
        )
    return len(listings)


def _lock_open_listings(db: Session, event_id: uuid.UUID) -> Sequence[Listing]:
    """The event's open listings, locked in id order. A transaction that holds several listings
    always takes them in id order; expire_reservations() does too, and they meet here."""
    return db.scalars(
        select(Listing)
        .where(Listing.event_id == event_id, Listing.status.in_(OPEN_LISTING_STATUSES))
        .order_by(Listing.id)
        .with_for_update(of=Listing)
        .execution_options(populate_existing=True)
    ).all()


def pull_back_listing_expiry(db: Session, event_id: uuid.UUID, starts_at: datetime) -> int:
    """After an event's start moves, no open listing may expire later than the new start.
    Not a status change, so no listing audit rows; the caller records the count."""
    _lock_open_listings(db, event_id)  # an UPDATE alone would lock them in any order
    result = db.execute(
        update(Listing)
        .where(
            Listing.event_id == event_id,
            Listing.status.in_(OPEN_LISTING_STATUSES),
            Listing.expires_at > starts_at,
        )
        .values(expires_at=starts_at, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


def push_forward_listing_expiry(
    db: Session, event_id: uuid.UUID, old_start: datetime, new_start: datetime
) -> int:
    """After an event is put back, open listings that ended when it started (the default for a
    listing created without an end) end at the new start instead. A listing the seller set to
    end earlier keeps its end. Like the pull-back, no listing audit rows; the caller counts."""
    _lock_open_listings(db, event_id)
    result = db.execute(
        update(Listing)
        .where(
            Listing.event_id == event_id,
            Listing.status.in_(OPEN_LISTING_STATUSES),
            Listing.expires_at == old_start,
        )
        .values(expires_at=new_start, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


def release_listing(
    db: Session,
    listing_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None,
    reason: str,
    now: datetime | None = None,
) -> Listing:
    """A hold ended without a sale: the RESERVED listing goes back on sale, or is EXPIRED if
    its own end passed meanwhile. `reason` shows in the seller's history."""
    listing = db.get_one(Listing, listing_id, populate_existing=True)
    ended = listing.expires_at <= (now or db.scalar(select(func.now())))
    return transition(
        db,
        listing,
        ListingStatus.EXPIRED if ended else ListingStatus.ACTIVE,
        actor_id=actor_id,
        details={"reason": reason},
    )


def expire_listings(db: Session, now: datetime | None = None) -> int:
    """Move every ACTIVE listing whose expiry has passed to EXPIRED, as the system (no actor).
    Returns how many. SKIP LOCKED: a listing someone is changing right now is left for the
    next run, and two runs at once never wait on each other."""
    now = now or db.scalar(select(func.now()))
    stmt = (
        select(Listing)
        .where(Listing.status == ListingStatus.ACTIVE, Listing.expires_at <= now)
        .order_by(Listing.id)
        .with_for_update(of=Listing, skip_locked=True)
        .execution_options(populate_existing=True)
    )
    listings = db.scalars(stmt).all()
    for listing in listings:
        transition(db, listing, ListingStatus.EXPIRED, actor_id=None)
    return len(listings)
