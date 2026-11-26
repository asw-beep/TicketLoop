"""Writing events, and remembering which consumer has handled which (hard problem 4).

    emit(db, aggregate_type="order", aggregate_id=order.id, event_type="payment.succeeded",
         payload={...})

inserts a row in the caller's transaction: the event exists exactly when the change does. The
relay (relay.py) publishes it later. Two systems cannot be written atomically, so the change
and the event are written to one, Postgres, and the broker hears about it afterwards.

A consumer calls `first_time(db, consumer, event_id)` inside the transaction of its effect.
False means it has done this before: the message was delivered again (the relay published it
twice, or a worker died before acknowledging it), and there is nothing to do.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.telemetry import current_context_headers
from app.modules.outbox.models import OutboxEvent, ProcessedEvent


class EventType(StrEnum):
    PAYMENT_SUCCEEDED = "payment.succeeded"
    PAYMENT_FAILED = "payment.failed"
    RESERVATION_EXPIRED = "reservation.expired"
    TRANSFER_INITIATED = "transfer.initiated"
    TRANSFER_COMPLETED = "transfer.completed"
    TRANSFER_FAILED = "transfer.failed"


class Consumer(StrEnum):
    """Each is a Celery task of the same name, on its own queue."""

    START_TRANSFER = "transfers.start"
    PERFORM_TRANSFER = "transfers.perform"
    NOTIFY = "notifications.notify"


# Who hears about what. The relay sends one message per consumer per event.
CONSUMERS: Mapping[str, tuple[Consumer, ...]] = {
    EventType.PAYMENT_SUCCEEDED: (Consumer.START_TRANSFER, Consumer.NOTIFY),
    EventType.PAYMENT_FAILED: (Consumer.NOTIFY,),
    EventType.RESERVATION_EXPIRED: (Consumer.NOTIFY,),
    EventType.TRANSFER_INITIATED: (Consumer.PERFORM_TRANSFER,),
    EventType.TRANSFER_COMPLETED: (Consumer.NOTIFY,),
    EventType.TRANSFER_FAILED: (Consumer.NOTIFY,),
}


def emit(
    db: Session,
    *,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: EventType,
    payload: Mapping[str, Any],
) -> uuid.UUID:
    """Add an event to the caller's transaction. Never commits. The current trace goes with
    it, so the relay and the worker continue the trace that caused it."""
    event = OutboxEvent(
        id=uuid.uuid4(),
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=str(event_type),
        payload={key: _json_safe(value) for key, value in payload.items()},
        headers=current_context_headers(),
    )
    db.add(event)
    db.flush()
    return event.id


def message(event: OutboxEvent) -> dict[str, Any]:
    """What a consumer receives: everything it needs, nothing it must look up to dedupe."""
    return {
        "id": str(event.id),
        "type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": str(event.aggregate_id),
        "payload": event.payload,
        "headers": event.headers,
    }


def first_time(db: Session, consumer: Consumer, event_id: uuid.UUID) -> bool:
    """Claim the event for this consumer, in the caller's transaction. A second delivery
    arriving while the first is still in its transaction waits here for it, then gets False."""
    claimed = db.execute(
        insert(ProcessedEvent)
        .values(consumer=str(consumer), event_id=event_id)
        .on_conflict_do_nothing()
        .returning(ProcessedEvent.event_id)
    ).scalar_one_or_none()
    return claimed is not None


def done_before(db: Session, consumer: Consumer, event_id: uuid.UUID) -> bool:
    """Whether this consumer has finished with the event, without claiming it."""
    return bool(
        db.scalar(
            select(
                exists().where(
                    ProcessedEvent.consumer == str(consumer), ProcessedEvent.event_id == event_id
                )
            )
        )
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value
