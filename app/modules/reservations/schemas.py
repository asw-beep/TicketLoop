"""Response bodies for /reservations. There is no request body: the listing is in the path, the
buyer is the caller, and the server decides the rest."""

import uuid

from pydantic import AwareDatetime

from app.core.schemas import ResponseModel
from app.modules.listings.schemas import ListingEventSummary
from app.modules.reservations.models import ReservationStatus


class ReservedListingOut(ResponseModel):
    """What is being held. The buyer cannot read GET /listings/{id} once the listing is
    RESERVED, so the reservation carries what its page shows. Not the listing's status or
    seller: a hold that ended gives no right to follow what happens to the ticket next."""

    id: uuid.UUID
    event: ListingEventSummary
    ticket_category: str
    seat_details: str | None
    asking_price_paise: int
    original_price_paise: int


class ReservationOut(ResponseModel):
    """`Reservation` in the API contract. Never says who the buyer is: the seller may see it."""

    id: uuid.UUID
    listing_id: uuid.UUID
    status: ReservationStatus
    expires_at: AwareDatetime
    created_at: AwareDatetime
    order_id: uuid.UUID | None
    listing: ReservedListingOut
