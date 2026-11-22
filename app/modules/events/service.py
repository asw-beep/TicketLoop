"""Event and ticket-provider logic (FR-02). Functions take the request's Session and never
commit; get_db commits once the route returns.

Event lifecycle (docs/state-machines.md):

    DRAFT -> PUBLISHED -> COMPLETED
    DRAFT | PUBLISHED -> CANCELLED

Publishing needs a start time in the future and completing one in the past. CANCELLED and
COMPLETED events are final: nothing about them changes again.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.pagination import paginate
from app.infrastructure.database.errors import violated_constraint
from app.modules.events.errors import (
    EventInPastError,
    EventNotEditableError,
    EventNotFoundError,
    IllegalStatusChangeError,
    ProviderCodeTakenError,
    ProviderNotFoundError,
)
from app.modules.events.models import Event, EventStatus, TicketProvider
from app.modules.events.schemas import EventIn, EventPatch, ProviderIn

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    EventStatus.DRAFT: {EventStatus.PUBLISHED, EventStatus.CANCELLED},
    EventStatus.PUBLISHED: {EventStatus.COMPLETED, EventStatus.CANCELLED},
    EventStatus.CANCELLED: set(),
    EventStatus.COMPLETED: set(),
}
FINAL_STATUSES = {EventStatus.CANCELLED, EventStatus.COMPLETED}

# Ties on start time are broken by id, so every page has a fixed order.
_BY_START = (Event.starts_at, Event.id)


def list_published_events(
    db: Session, cursor: str | None, limit: int
) -> tuple[list[Event], str | None]:
    stmt = select(Event).where(Event.status == EventStatus.PUBLISHED).order_by(*_BY_START)
    return paginate(db, stmt, cursor, limit)


def get_published_event(db: Session, event_id: uuid.UUID) -> Event:
    """A published event. Drafts and every other status are 404 to the public."""
    event = db.get(Event, event_id)
    if event is None or event.status != EventStatus.PUBLISHED:
        raise EventNotFoundError()
    return event


def list_all_events(db: Session, cursor: str | None, limit: int) -> tuple[list[Event], str | None]:
    return paginate(db, select(Event).order_by(*_BY_START), cursor, limit)


def create_event(db: Session, data: EventIn) -> Event:
    _require_future(data.starts_at)
    _require_provider(db, data.provider_id)
    event = Event(**data.model_dump())
    db.add(event)
    db.flush()  # fills id and the DRAFT default
    db.refresh(event)
    return event


def update_event(db: Session, event_id: uuid.UUID, data: EventPatch) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise EventNotFoundError()
    changes = data.model_dump(exclude_unset=True)
    seen_status = event.status

    if seen_status in FINAL_STATUSES:
        raise EventNotEditableError(f"The event is {seen_status}.")
    if "provider_id" in changes and changes["provider_id"] != event.provider_id:
        if seen_status != EventStatus.DRAFT:
            raise EventNotEditableError("The provider can change only while the event is DRAFT.")
        _require_provider(db, changes["provider_id"])
    if "starts_at" in changes:
        _require_future(changes["starts_at"])

    new_status = changes.get("status")
    if new_status is not None and new_status != seen_status:
        _check_transition(seen_status, new_status, changes.get("starts_at", event.starts_at))
    if not changes:
        return event

    # One conditional UPDATE: it applies only if the status is still the one we checked
    # against, so two admins acting at once cannot both win (docs/state-machines.md).
    updated = db.execute(
        update(Event)
        .where(Event.id == event_id, Event.status == seen_status)
        .values(**changes, updated_at=datetime.now(UTC))
        .returning(Event.id)
    ).scalar_one_or_none()
    if updated is None:
        raise IllegalStatusChangeError("The event changed while you were editing it.")
    db.refresh(event)
    return event


def list_providers(db: Session) -> list[TicketProvider]:
    return list(db.scalars(select(TicketProvider).order_by(TicketProvider.code)))


def create_provider(db: Session, data: ProviderIn) -> TicketProvider:
    provider = TicketProvider(code=data.code, name=data.name)
    db.add(provider)
    try:
        with db.begin_nested():  # a SAVEPOINT, so a duplicate leaves the request usable
            db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == "uq_ticket_providers_code":
            raise ProviderCodeTakenError() from None
        raise
    return provider


def _check_transition(current: str, new: str, starts_at: datetime) -> None:
    if new not in ALLOWED_TRANSITIONS[current]:
        raise IllegalStatusChangeError(f"An event cannot go from {current} to {new}.")
    now = datetime.now(UTC)
    if new == EventStatus.PUBLISHED and starts_at <= now:
        raise IllegalStatusChangeError("Only an event that has not started can be published.")
    if new == EventStatus.COMPLETED and starts_at > now:
        raise IllegalStatusChangeError("Only an event that has started can be completed.")


def _require_future(starts_at: datetime) -> None:
    if starts_at <= datetime.now(UTC):
        raise EventInPastError()


def _require_provider(db: Session, provider_id: uuid.UUID) -> None:
    if db.get(TicketProvider, provider_id) is None:
        raise ProviderNotFoundError()
