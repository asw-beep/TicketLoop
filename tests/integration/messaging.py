"""Helpers for the outbox, transfer and notification tests.

    from tests.integration.messaging import sessions  # noqa: F401

`sessions` is a session factory on the test's own connection: each commit is a savepoint
release, and everything is rolled back afterwards, as with db_session. Consumers and the relay
take a factory because in production each step commits on its own.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Connection, select
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.database.session import create_session_factory
from app.modules.outbox.models import OutboxEvent
from app.modules.outbox.service import Consumer, message
from app.modules.payments import service as payments
from tests.integration.factories import make_payment


@pytest.fixture
def sessions(db_connection: Connection) -> Iterator[sessionmaker[Session]]:
    factory = create_session_factory(db_connection)
    factory.configure(join_transaction_mode="create_savepoint")
    yield factory


def events(db: Session, event_type: str | None = None) -> list[OutboxEvent]:
    stmt = select(OutboxEvent).order_by(OutboxEvent.created_at, OutboxEvent.id)
    if event_type is not None:
        stmt = stmt.where(OutboxEvent.event_type == event_type)
    return list(db.scalars(stmt))


def last_message(db: Session, event_type: str) -> dict[str, Any]:
    """The newest event of this type, as a consumer receives it."""
    return message(events(db, event_type)[-1])


def paid(sessions: sessionmaker[Session]) -> tuple[uuid.UUID, dict[str, Any]]:
    """An order paid for through the real settling code, committed: its id and its
    payment.succeeded message."""
    with sessions() as db, db.begin():
        payment = make_payment(db, status="PENDING")
        assert payments.succeed(db, payment.id, source="test", snapshot={})
        return payment.order_id, last_message(db, "payment.succeeded")


class RecordingPublisher:
    """Keeps what it was asked to publish. `fail_on` makes the publish of that consumer, or of
    every consumer when it is "*", fail."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.sent: list[tuple[Consumer, dict[str, Any]]] = []
        self.fail_on = fail_on

    def publish(self, consumer: Consumer, msg: dict[str, Any]) -> None:
        from app.modules.outbox.relay import PublishError

        if self.fail_on in ("*", consumer.value):
            raise PublishError("broker unreachable")
        self.sent.append((consumer, msg))

    def ids(self) -> list[tuple[str, uuid.UUID]]:
        return [(consumer.value, uuid.UUID(msg["id"])) for consumer, msg in self.sent]
