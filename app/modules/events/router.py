"""HTTP routes for events and ticket providers. Thin: validate, call the service, shape."""

import uuid

from fastapi import APIRouter, status

from app.core.pagination import Cursor, Limit, Page
from app.infrastructure.database.session import DbSession
from app.modules.events import service
from app.modules.events.schemas import EventIn, EventOut, EventPatch, ProviderIn, ProviderOut
from app.modules.identity.dependencies import AdminUser

events_router = APIRouter(prefix="/events", tags=["events"])
admin_events_router = APIRouter(prefix="/admin", tags=["admin: events"])


@events_router.get("", response_model=Page[EventOut])
def list_events(db: DbSession, cursor: Cursor = None, limit: Limit = 20) -> Page[EventOut]:
    """Published events, soonest first. Filters and search arrive in M6."""
    items, next_cursor = service.list_published_events(db, cursor, limit)
    return Page[EventOut](items=items, next_cursor=next_cursor)


@events_router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: uuid.UUID, db: DbSession) -> EventOut:
    return EventOut.model_validate(service.get_published_event(db, event_id))


@admin_events_router.get("/events", response_model=Page[EventOut])
def admin_list_events(
    admin: AdminUser, db: DbSession, cursor: Cursor = None, limit: Limit = 20
) -> Page[EventOut]:
    """Every event in every status, soonest first."""
    items, next_cursor = service.list_all_events(db, cursor, limit)
    return Page[EventOut](items=items, next_cursor=next_cursor)


@admin_events_router.post("/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create_event(body: EventIn, admin: AdminUser, db: DbSession) -> EventOut:
    return EventOut.model_validate(service.create_event(db, body))


@admin_events_router.patch("/events/{event_id}", response_model=EventOut)
def update_event(
    event_id: uuid.UUID, body: EventPatch, admin: AdminUser, db: DbSession
) -> EventOut:
    return EventOut.model_validate(service.update_event(db, event_id, body))


@admin_events_router.get("/providers", response_model=list[ProviderOut])
def list_providers(admin: AdminUser, db: DbSession) -> list[ProviderOut]:
    return [ProviderOut.model_validate(p) for p in service.list_providers(db)]


@admin_events_router.post(
    "/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED
)
def create_provider(body: ProviderIn, admin: AdminUser, db: DbSession) -> ProviderOut:
    return ProviderOut.model_validate(service.create_provider(db, body))
