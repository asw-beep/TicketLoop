"""Request and response bodies for /orders."""

import uuid
from typing import Literal

from pydantic import AwareDatetime

from app.core.schemas import RequestModel, ResponseModel
from app.modules.orders.models import OrderStatus
from app.modules.reservations.schemas import ReservedListingOut
from app.modules.transfers.schemas import TransferSummaryOut

OrderRole = Literal["buyer", "seller"]


class OrderIn(RequestModel):
    """There is no amount: the server copies it from the listing."""

    reservation_id: uuid.UUID


class OrderOut(ResponseModel):
    """`Order` in the API contract. Says neither who the buyer is nor who the seller is."""

    id: uuid.UUID
    listing_id: uuid.UUID
    reservation_id: uuid.UUID
    amount_paise: int
    currency: str
    status: OrderStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime
    listing: ReservedListingOut
    # From M9: how far the ticket has got, once the order is paid. Null before.
    transfer: TransferSummaryOut | None
