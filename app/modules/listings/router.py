"""HTTP routes for /listings and /admin/listings. Thin: validate, call the service, shape."""

import uuid

from fastapi import APIRouter

from app.core.pagination import Cursor, Limit, Page
from app.infrastructure.database.session import DbSession
from app.modules.identity.dependencies import AdminUser, CurrentUser, OptionalUser
from app.modules.listings import service
from app.modules.listings.models import ListingStatus
from app.modules.listings.schemas import (
    HistoryEntryOut,
    ListingIn,
    ListingOut,
    ListingPatch,
    PublicListingOut,
    RejectIn,
)

listings_router = APIRouter(prefix="/listings", tags=["listings"])
admin_listings_router = APIRouter(prefix="/admin/listings", tags=["admin: listings"])


@listings_router.post("", response_model=ListingOut, status_code=201)
def create_listing(body: ListingIn, user: CurrentUser, db: DbSession) -> ListingOut:
    """Offer a ticket you own at no more than face value. It starts as a DRAFT; submit it to
    send it for review."""
    return ListingOut.model_validate(service.create_listing(db, user, body))


# Declared before /{listing_id}, or "me" would be read as a (malformed) listing ID.
@listings_router.get("/me", response_model=Page[ListingOut])
def my_listings(
    user: CurrentUser,
    db: DbSession,
    status: ListingStatus | None = None,
    ticket_id: uuid.UUID | None = None,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> Page[ListingOut]:
    """Your listings in every status, newest first."""
    items, next_cursor = service.list_my_listings(db, user, status, ticket_id, cursor, limit)
    return Page[ListingOut](items=items, next_cursor=next_cursor)


@listings_router.get("/{listing_id}", response_model=ListingOut | PublicListingOut)
def get_listing(
    listing_id: uuid.UUID, user: OptionalUser, db: DbSession
) -> ListingOut | PublicListingOut:
    """An ACTIVE listing, for anyone, in the public shape. The seller and admins see any
    status, in the full shape (it has `ticket_id`). Signing in is optional."""
    listing = service.get_listing_for(db, listing_id, user)
    if service.can_see_everything(listing, user):
        return ListingOut.model_validate(listing)
    return PublicListingOut.model_validate(listing)


@listings_router.patch("/{listing_id}", response_model=ListingOut)
def update_listing(
    listing_id: uuid.UUID, body: ListingPatch, user: CurrentUser, db: DbSession
) -> ListingOut:
    """Change your listing's price or expiry while it is DRAFT, PENDING_VERIFICATION or
    ACTIVE. An ACTIVE listing's price can only go down."""
    return ListingOut.model_validate(service.update_listing(db, listing_id, user, body))


@listings_router.post("/{listing_id}/submit", response_model=ListingOut)
def submit_listing(listing_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ListingOut:
    """Send your DRAFT listing for review."""
    return ListingOut.model_validate(service.submit_listing(db, listing_id, user))


@listings_router.delete("/{listing_id}", response_model=ListingOut)
def cancel_listing(listing_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ListingOut:
    """Cancel a listing. The seller can while it is DRAFT, PENDING_VERIFICATION or ACTIVE;
    an admin can cancel anyone's ACTIVE listing. The row is kept, as CANCELLED."""
    return ListingOut.model_validate(service.cancel_listing(db, listing_id, user))


@listings_router.get("/{listing_id}/history", response_model=Page[HistoryEntryOut])
def listing_history(
    listing_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> Page[HistoryEntryOut]:
    """What happened to the listing, oldest first. For its seller and admins."""
    items, next_cursor = service.list_history(db, listing_id, user, cursor, limit)
    return Page[HistoryEntryOut](items=items, next_cursor=next_cursor)


@admin_listings_router.get("/pending", response_model=Page[ListingOut])
def pending_listings(
    admin: AdminUser, db: DbSession, cursor: Cursor = None, limit: Limit = 20
) -> Page[ListingOut]:
    """Listings waiting for review, oldest first."""
    items, next_cursor = service.list_pending_listings(db, cursor, limit)
    return Page[ListingOut](items=items, next_cursor=next_cursor)


@admin_listings_router.get("", response_model=Page[ListingOut])
def all_listings(
    admin: AdminUser,
    db: DbSession,
    status: ListingStatus | None = None,
    ticket_id: uuid.UUID | None = None,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> Page[ListingOut]:
    """Every listing in every status, newest first."""
    items, next_cursor = service.list_all_listings(db, status, ticket_id, cursor, limit)
    return Page[ListingOut](items=items, next_cursor=next_cursor)


@admin_listings_router.post("/{listing_id}/approve", response_model=ListingOut)
def approve_listing(listing_id: uuid.UUID, admin: AdminUser, db: DbSession) -> ListingOut:
    """Put a listing waiting for review on sale. Not your own."""
    return ListingOut.model_validate(service.approve_listing(db, listing_id, admin))


@admin_listings_router.post("/{listing_id}/reject", response_model=ListingOut)
def reject_listing(
    listing_id: uuid.UUID, body: RejectIn, admin: AdminUser, db: DbSession
) -> ListingOut:
    """Reject a listing waiting for review, with a reason the seller sees. Not your own."""
    return ListingOut.model_validate(service.reject_listing(db, listing_id, admin, body.reason))
