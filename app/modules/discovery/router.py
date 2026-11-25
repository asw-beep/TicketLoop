"""Public search routes. No login, and only the public listing shape."""

import uuid
from dataclasses import replace

from fastapi import APIRouter

from app.core.pagination import Cursor, Limit, Page
from app.infrastructure.database.session import DbSession
from app.modules.discovery import service
from app.modules.discovery.filters import Filters, ListingSort, SortBy
from app.modules.events.service import get_published_event
from app.modules.listings.schemas import PublicListingOut

discovery_router = APIRouter(tags=["discovery"])


@discovery_router.get("/listings", response_model=Page[PublicListingOut])
def search_listings(
    filters: Filters,
    db: DbSession,
    sort: SortBy = ListingSort.PRICE,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> Page[PublicListingOut]:
    """Tickets on sale now, cheapest first unless you ask otherwise."""
    items, next_cursor = service.search_listings(db, filters, sort, cursor, limit)
    return Page[PublicListingOut](items=items, next_cursor=next_cursor)


@discovery_router.get("/events/{event_id}/listings", response_model=Page[PublicListingOut])
def event_listings(
    event_id: uuid.UUID,
    filters: Filters,
    db: DbSession,
    sort: SortBy = ListingSort.PRICE,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> Page[PublicListingOut]:
    """One published event's tickets on sale now. Same filters and sorts as `GET /listings`."""
    event = get_published_event(db, event_id)
    filters = replace(filters, event_id=event.id)
    items, next_cursor = service.search_listings(db, filters, sort, cursor, limit)
    return Page[PublicListingOut](items=items, next_cursor=next_cursor)
