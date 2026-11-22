"""Request and response bodies for /tickets and /admin/tickets."""

import uuid
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from app.core.schemas import RequestModel, ResponseModel
from app.modules.tickets.models import DocumentType, TicketStatus, VerificationStatus

# Trimmed text. Whitespace-only fails min_length once trimmed, so "   " is a 422.
Category = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
SeatDetails = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Reference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
# 1 paise to ₹1,00,000. The ceiling catches typos such as an extra zero.
PricePaise = Annotated[int, Field(ge=1, le=10_000_000)]


class TicketIn(RequestModel):
    """The owner is never in the body: it is always the caller (unknown fields are 422)."""

    event_id: uuid.UUID
    ticket_category: Category
    seat_details: SeatDetails | None = None
    original_price_paise: PricePaise
    purchase_reference: Reference
    provider_reference: Reference


class EventSummary(ResponseModel):
    id: uuid.UUID
    name: str
    venue: str
    city: str
    starts_at: AwareDatetime


class TicketOut(ResponseModel):
    """`Ticket` in the API contract. Every M4 ticket route is for the owner or an admin, so
    the private references are always included; M5's public listing shape leaves them out."""

    id: uuid.UUID
    event_id: uuid.UUID
    event: EventSummary
    ticket_category: str
    seat_details: str | None
    original_price_paise: int
    purchase_reference: str
    provider_reference: str
    verification_status: VerificationStatus
    verification_note: str | None
    transfer_eligible: bool
    status: TicketStatus
    created_at: AwareDatetime


class DocumentOut(ResponseModel):
    """What the API says about an evidence file. Never its storage key or original name."""

    id: uuid.UUID
    content_type: DocumentType
    size_bytes: int
    created_at: AwareDatetime


class DocumentLinkOut(ResponseModel):
    """A presigned link to open the file. Anyone holding it can, until expires_at."""

    url: str
    expires_at: AwareDatetime


Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class VerifyIn(RequestModel):
    """An admin's decision on a PENDING ticket. A rejection must say why: the owner sees it."""

    decision: Literal["VERIFIED", "REJECTED"]
    transfer_eligible: bool = False
    note: Note | None = None

    @model_validator(mode="after")
    def _rejection_has_a_reason(self) -> Self:
        if self.decision == "REJECTED" and self.note is None:
            raise ValueError("a rejection needs a note explaining why")
        return self
