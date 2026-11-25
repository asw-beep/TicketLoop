"""Finding listings to buy (FR-05). Read only: nothing here decides who gets a ticket."""

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, contains_eager

from app.core.pagination import paginate
from app.core.search import LIKE_ESCAPE, contains
from app.modules.discovery.filters import LISTING_SORTS, ListingFilters, ListingSort
from app.modules.events.models import Event
from app.modules.listings.models import Listing, ListingStatus
from app.modules.tickets.models import Ticket


def search_stmt(filters: ListingFilters) -> Select[tuple[Listing]]:
    """Listings a buyer could reserve right now, narrowed by `filters`.

    `expires_at > now()` as well as ACTIVE: the expiry job runs every few minutes, and until it
    does, a listing past its end is still ACTIVE in the table but can no longer be reserved.
    """
    stmt = (
        select(Listing)
        .join(Listing.event)
        .join(Listing.ticket)
        # One join each, serving both the filters and the response. A ticket loads its own event
        # eagerly by default; here that would join events a second time for nothing.
        .options(
            contains_eager(Listing.event),
            contains_eager(Listing.ticket).lazyload(Ticket.event),
        )
        .where(Listing.status == ListingStatus.ACTIVE, Listing.expires_at > func.now())
    )
    if filters.event_id is not None:
        stmt = stmt.where(Listing.event_id == filters.event_id)
    if filters.q is not None:
        stmt = stmt.where(Event.name.ilike(contains(filters.q), escape=LIKE_ESCAPE))
    if filters.category is not None:
        stmt = stmt.where(Event.category == filters.category)
    if filters.city is not None:
        stmt = stmt.where(func.lower(Event.city) == func.lower(filters.city))
    if filters.ticket_category is not None:
        stmt = stmt.where(func.lower(Ticket.ticket_category) == func.lower(filters.ticket_category))
    if filters.min_price_paise is not None:
        stmt = stmt.where(Listing.asking_price_paise >= filters.min_price_paise)
    if filters.max_price_paise is not None:
        stmt = stmt.where(Listing.asking_price_paise <= filters.max_price_paise)
    return stmt


def search_listings(
    db: Session, filters: ListingFilters, sort: ListingSort, cursor: str | None, limit: int
) -> tuple[list[Listing], str | None]:
    return paginate(db, search_stmt(filters), LISTING_SORTS[sort], cursor, limit)
