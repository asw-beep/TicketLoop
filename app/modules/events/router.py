"""HTTP routes for events and ticket providers. Thin: validate, call the service, shape."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import AwareDatetime

from app.api.caching import cached_json
from app.core.pagination import Cursor, Limit, Page
from app.core.search import SearchText
from app.infrastructure.database.session import DbSession
from app.modules.events import service
from app.modules.events.models import EventCategory
from app.modules.events.schemas import EventIn, EventOut, EventPatch, ProviderIn, ProviderOut
from app.modules.identity.dependencies import AdminUser

events_router = APIRouter(prefix="/events", tags=["events"])
admin_events_router = APIRouter(prefix="/admin", tags=["admin: events"])


@events_router.get("", response_model=Page[EventOut])
def list_events(
    db: DbSession,
    q: Annotated[SearchText | None, Query(description="Part of the event's name")] = None,
    category: EventCategory | None = None,
    city: SearchText | None = None,
    # AwareDatetime: a time without a timezone is a 422, never guessed to be UTC.
    starts_after: AwareDatetime | None = None,
    starts_before: AwareDatetime | None = None,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> Page[EventOut]:
    """Published events, soonest first."""
    filters = service.EventFilters(
        q=q or None,
        category=category,
        city=city or None,
        starts_after=starts_after,
        starts_before=starts_before,
    )
    items, next_cursor = service.list_published_events(db, filters, cursor, limit)
    return Page[EventOut](items=items, next_cursor=next_cursor)


@events_router.get("/{event_id}", response_model=EventOut)
def get_event(request: Request, event_id: uuid.UUID, db: DbSession) -> Response:
    """A published event. Cached (M10) until any event changes; a 404 is never cached."""
    return cached_json(
        request,
        name="event",
        depends_on=("events",),
        params={"event_id": event_id},
        compute=lambda: EventOut.model_validate(service.get_published_event(db, event_id)),
    )


@admin_events_router.get("/events", response_model=Page[EventOut])
def admin_list_events(
    admin: AdminUser, db: DbSession, cursor: Cursor = None, limit: Limit = 20
) -> Page[EventOut]:
    """Every event in every status, soonest first."""
    items, next_cursor = service.list_all_events(db, cursor, limit)
    return Page[EventOut](items=items, next_cursor=next_cursor)


@admin_events_router.post("/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create_event(body: EventIn, admin: AdminUser, db: DbSession) -> EventOut:
    return EventOut.model_validate(service.create_event(db, body, admin))


@admin_events_router.patch("/events/{event_id}", response_model=EventOut)
def update_event(
    event_id: uuid.UUID, body: EventPatch, admin: AdminUser, db: DbSession
) -> EventOut:
    return EventOut.model_validate(service.update_event(db, event_id, body, admin))


@admin_events_router.get("/providers", response_model=list[ProviderOut])
def list_providers(admin: AdminUser, db: DbSession) -> list[ProviderOut]:
    return [ProviderOut.model_validate(p) for p in service.list_providers(db)]


@admin_events_router.post(
    "/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED
)
def create_provider(body: ProviderIn, admin: AdminUser, db: DbSession) -> ProviderOut:
    return ProviderOut.model_validate(service.create_provider(db, body, admin))
