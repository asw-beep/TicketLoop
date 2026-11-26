"""Public search routes. No login, and only the public listing shape.

The first page of each search is cached (M10): it is what most people look at, and the
cheapest to keep right, since any listing or event change makes it unreachable. Later pages
come from Postgres every time.
"""

import uuid
from dataclasses import asdict, replace

from fastapi import APIRouter, Depends, Request, Response

from app.api.caching import cached_json, uncached
from app.api.rate_limits import per_address
from app.core.pagination import Cursor, Limit, Page
from app.infrastructure.database.session import DbSession
from app.modules.discovery import service
from app.modules.discovery.filters import Filters, ListingSort, SortBy
from app.modules.events.service import get_published_event
from app.modules.listings.schemas import PublicListingOut

discovery_router = APIRouter(tags=["discovery"], dependencies=[Depends(per_address("search"))])


@discovery_router.get("/listings", response_model=Page[PublicListingOut])
def search_listings(
    request: Request,
    filters: Filters,
    db: DbSession,
    sort: SortBy = ListingSort.PRICE,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> Response:
    """Tickets on sale now, cheapest first unless you ask otherwise."""

    def page() -> Page[PublicListingOut]:
        items, next_cursor = service.search_listings(db, filters, sort, cursor, limit)
        return Page[PublicListingOut](items=items, next_cursor=next_cursor)

    if cursor is not None:
        return uncached(page)
    return cached_json(
        request,
        name="search",
        depends_on=("listings",),
        params=asdict(filters) | {"sort": sort, "limit": limit},
        compute=page,
    )


@discovery_router.get("/events/{event_id}/listings", response_model=Page[PublicListingOut])
def event_listings(
    request: Request,
    event_id: uuid.UUID,
    filters: Filters,
    db: DbSession,
    sort: SortBy = ListingSort.PRICE,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> Response:
    """One published event's tickets on sale now. Same filters and sorts as `GET /listings`."""

    def page() -> Page[PublicListingOut]:
        event = get_published_event(db, event_id)  # 404 unless published, never cached
        items, next_cursor = service.search_listings(
            db, replace(filters, event_id=event.id), sort, cursor, limit
        )
        return Page[PublicListingOut](items=items, next_cursor=next_cursor)

    if cursor is not None:
        return uncached(page)
    return cached_json(
        request,
        name="event-search",
        depends_on=("events", "listings"),
        params=asdict(replace(filters, event_id=event_id)) | {"sort": sort, "limit": limit},
        compute=page,
    )
