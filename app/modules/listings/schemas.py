"""Request and response bodies for /listings and /admin/listings.

Two response shapes. `PublicListingOut` is what anyone may see of an ACTIVE listing: never the
seller, the ticket's references or its evidence. `ListingOut` adds what the seller and admins
need. `GET /listings/{id}` answers with one or the other; a client tells them apart by
`ticket_id`, which only the full shape has.
"""

import uuid
from typing import Annotated, Any, Literal, Self

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from app.core.schemas import RequestModel, ResponseModel
from app.modules.listings.models import ListingStatus

# The schema's sanity bounds. The real rule, ₹1 to the ticket's face value, needs the ticket
# and is check_price()'s 422 price-out-of-range.
PricePaise = Annotated[int, Field(ge=1, le=10_000_000)]


class ListingIn(RequestModel):
    """The seller is never in the body: it is always the caller (unknown fields are 422).
    Without `expires_at` the listing expires when the event starts."""

    ticket_id: uuid.UUID
    asking_price_paise: PricePaise
    expires_at: AwareDatetime | None = None


class ListingPatch(RequestModel):
    """Change the price, the expiry, or both. At least one, and neither may be null."""

    asking_price_paise: PricePaise | None = None
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("send asking_price_paise, expires_at, or both")
        nulls = sorted(f for f in self.model_fields_set if getattr(self, f) is None)
        if nulls:
            raise ValueError(f"{', '.join(nulls)} cannot be null")
        return self


class RejectIn(RequestModel):
    """Why the listing was rejected. The seller sees it. Trimmed, so "   " is a 422."""

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ListingEventSummary(ResponseModel):
    id: uuid.UUID
    name: str
    category: str
    city: str
    venue: str
    starts_at: AwareDatetime


class PublicListingOut(ResponseModel):
    """`PublicListing` in the API contract."""

    id: uuid.UUID
    event: ListingEventSummary
    ticket_category: str
    seat_details: str | None
    asking_price_paise: int
    original_price_paise: int
    status: ListingStatus
    created_at: AwareDatetime


class ListingOut(PublicListingOut):
    """`Listing` in the API contract: for the seller and admins only."""

    ticket_id: uuid.UUID
    seller_id: uuid.UUID
    expires_at: AwareDatetime
    reviewed_at: AwareDatetime | None
    rejection_reason: str | None
    updated_at: AwareDatetime


class HistoryEntryOut(ResponseModel):
    """One line of a listing's history. Who acted is a role, never an identity: the seller
    must not learn which admin reviewed their listing."""

    action: str
    from_status: ListingStatus | None
    to_status: ListingStatus | None
    actor_role: Literal["seller", "admin", "system"]
    details: dict[str, Any]
    created_at: AwareDatetime
