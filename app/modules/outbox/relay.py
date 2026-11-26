"""The relay: from the outbox table to the broker (hard problem 4).

Each pass claims a batch of due, unpublished events with FOR UPDATE SKIP LOCKED, so any number
of relays can run at once: each skips what another has claimed instead of waiting for it. For
every event it sends one message per consumer and waits for the broker to confirm each one.
Only then is the event marked published, in the same transaction that claimed it.

What can go wrong, and why nothing is lost or applied twice:

- The broker is down: the publish fails, the event's retry_count goes up and it waits a growing
  time before the next try. The rest of the batch is left for the next pass, untouched.
- The broker confirmed, then this process died before its commit: the event is still
  unpublished and is sent again. Consumers see a duplicate, and processed_events makes it
  harmless. At-least-once delivery plus idempotent consumers is exactly-once in effect.
- One of an event's two messages went out and the second failed: both are sent again later,
  and the first consumer ignores its copy for the same reason.
"""

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.metrics import OUTBOX_FAILURES, OUTBOX_PUBLISHED
from app.core.telemetry import continued
from app.modules.outbox.models import OutboxEvent
from app.modules.outbox.service import CONSUMERS, Consumer, message

log = structlog.get_logger(__name__)

BATCH = 100
MAX_BACKOFF_SECONDS = 300


class PublishError(Exception):
    """The broker did not confirm the message."""


class Publisher(Protocol):
    def publish(self, consumer: Consumer, message: dict[str, Any]) -> None:
        """Send one message to the consumer's queue and wait for the broker's confirm. Raises
        PublishError if it is not confirmed."""
        ...


@dataclass
class RelayReport:
    claimed: int = 0
    published: int = 0
    failed: int = 0
    error: str | None = None


def backoff(retry_count: int, rand: Callable[[], float] = random.random) -> timedelta:
    """2, 4, 8 ... seconds, at most five minutes, each cut to a random 50 to 100% of itself, so
    events that failed together do not all come back together."""
    ceiling = min(2**retry_count, MAX_BACKOFF_SECONDS)
    return timedelta(seconds=ceiling * (0.5 + rand() / 2))


def _span_attributes(event: OutboxEvent) -> dict[str, str]:
    found = {"ticketloop.event_id": str(event.id)}
    order_id = event.payload.get("order_id") if isinstance(event.payload, dict) else None
    if isinstance(order_id, str):
        found["ticketloop.order_id"] = order_id
    return found


def relay_once(
    sessions: sessionmaker[Session], publisher: Publisher, *, batch: int = BATCH
) -> RelayReport:
    """One pass. Returns what it did; `claimed == batch` means there is probably more."""
    report = RelayReport()
    with sessions() as db, db.begin():
        events = db.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None), OutboxEvent.next_attempt_at <= func.now())
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .limit(batch)
            .with_for_update(skip_locked=True)
        ).all()
        report.claimed = len(events)
        for event in events:
            try:
                # In the trace the event was emitted in; Celery carries it on to the worker.
                with continued(
                    event.headers, f"outbox publish {event.event_type}", _span_attributes(event)
                ):
                    for consumer in CONSUMERS.get(event.event_type, ()):
                        publisher.publish(consumer, message(event))
            except PublishError as error:
                OUTBOX_FAILURES.inc()
                event.retry_count += 1
                event.next_attempt_at = func.now() + backoff(event.retry_count)
                event.last_error = str(error)[:1000]
                report.failed += 1
                report.error = str(error)
                log.warning(
                    "outbox.publish_failed",
                    event_id=str(event.id),
                    retry_count=event.retry_count,
                    error=str(error),
                )
                # Most likely the broker is down, and the next event would only fail too.
                break
            event.published_at = func.now()
            report.published += 1
            OUTBOX_PUBLISHED.inc()
    return report
