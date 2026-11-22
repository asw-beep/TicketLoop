"""Request and response bodies for /events, /admin/events and /admin/providers."""

import uuid
from typing import Annotated, Self

from pydantic import AwareDatetime, StringConstraints, model_validator

from app.core.schemas import RequestModel, ResponseModel
from app.modules.events.models import EventCategory, EventStatus

# Trimmed text. Whitespace-only fails min_length once trimmed, so "   " is a 422.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)]
Venue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
City = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Organizer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ProviderName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


class EventIn(RequestModel):
    name: Name
    description: Description = ""
    category: EventCategory
    venue: Venue
    city: City
    # AwareDatetime: a time without a timezone is a 422, never guessed to be UTC.
    starts_at: AwareDatetime
    organizer: Organizer
    provider_id: uuid.UUID


class EventPatch(RequestModel):
    """Any subset of the event's fields, plus a status change checked by the state machine."""

    name: Name | None = None
    description: Description | None = None
    category: EventCategory | None = None
    venue: Venue | None = None
    city: City | None = None
    starts_at: AwareDatetime | None = None
    organizer: Organizer | None = None
    provider_id: uuid.UUID | None = None
    status: EventStatus | None = None

    @model_validator(mode="after")
    def _no_nulls(self) -> Self:
        # Leave a field out to keep it; every event field is required, so null is an error,
        # not a way to clear it.
        nulls = sorted(name for name in self.model_fields_set if getattr(self, name) is None)
        if nulls:
            raise ValueError(f"leave a field out instead of sending null: {', '.join(nulls)}")
        return self


class EventOut(ResponseModel):
    id: uuid.UUID
    name: str
    description: str
    category: EventCategory
    venue: str
    city: str
    starts_at: AwareDatetime
    organizer: str
    provider_id: uuid.UUID
    status: EventStatus


ProviderCode = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{2,50}$")]


class ProviderIn(RequestModel):
    code: ProviderCode
    name: ProviderName


class ProviderOut(ResponseModel):
    id: uuid.UUID
    code: str
    name: str
