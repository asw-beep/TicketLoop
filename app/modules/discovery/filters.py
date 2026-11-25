"""What a buyer may filter and sort by, read from the query string."""

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Query

from app.core.pagination import Sort
from app.core.search import SearchText
from app.modules.discovery.errors import BadPriceRangeError
from app.modules.events.models import EventCategory
from app.modules.listings.models import Listing

# The most a price filter accepts: ₹1,00,000, the most a ticket may cost.
MAX_PRICE_PAISE = 10_000_000


class ListingSort(StrEnum):
    PRICE = "price"
    PRICE_DOWN = "-price"
    OLDEST = "created_at"
    NEWEST = "-created_at"


_BY_PRICE = (Listing.asking_price_paise, Listing.id)
_BY_CREATED = (Listing.created_at, Listing.id)

LISTING_SORTS: dict[ListingSort, Sort] = {
    ListingSort.PRICE: Sort("search.price", _BY_PRICE),
    ListingSort.PRICE_DOWN: Sort("search.-price", _BY_PRICE, descending=True),
    ListingSort.OLDEST: Sort("search.created_at", _BY_CREATED),
    ListingSort.NEWEST: Sort("search.-created_at", _BY_CREATED, descending=True),
}


@dataclass(frozen=True)
class ListingFilters:
    q: str | None = None
    category: EventCategory | None = None
    city: str | None = None
    ticket_category: str | None = None
    min_price_paise: int | None = None
    max_price_paise: int | None = None
    event_id: uuid.UUID | None = None


Price = Annotated[int | None, Query(ge=0, le=MAX_PRICE_PAISE)]
SortBy = Annotated[
    ListingSort, Query(description="`price` and `created_at` ascend; a leading `-` descends")
]


def listing_filters(
    q: Annotated[SearchText | None, Query(description="Part of the event's name")] = None,
    category: EventCategory | None = None,
    city: SearchText | None = None,
    ticket_category: SearchText | None = None,
    min_price_paise: Price = None,
    max_price_paise: Price = None,
    event_id: uuid.UUID | None = None,
) -> ListingFilters:
    if (
        min_price_paise is not None
        and max_price_paise is not None
        and min_price_paise > max_price_paise
    ):
        raise BadPriceRangeError(
            f"min_price_paise ({min_price_paise}) is above max_price_paise ({max_price_paise})."
        )
    return ListingFilters(
        q=q or None,
        category=category,
        city=city or None,
        ticket_category=ticket_category or None,
        min_price_paise=min_price_paise,
        max_price_paise=max_price_paise,
        event_id=event_id,
    )


Filters = Annotated[ListingFilters, Depends(listing_filters)]
