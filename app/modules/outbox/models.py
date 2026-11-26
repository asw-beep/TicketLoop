"""The transactional outbox (hard problem 4): events written with the change they announce, and
which consumer has already handled which event."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, UUIDPrimaryKey


class OutboxEvent(UUIDPrimaryKey, Base):
    """One event, inserted in the transaction of the change it announces. Its id is the message
    id every consumer deduplicates on."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("retry_count >= 0", name="retry_count"),
        # The relay's question: what is due and not yet published, oldest first.
        Index(
            "ix_outbox_events_unpublished",
            "next_attempt_at",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    aggregate_type: Mapped[str]  # 'order', 'reservation', 'transfer'
    aggregate_id: Mapped[uuid.UUID]
    event_type: Mapped[str]  # 'payment.succeeded', ...
    payload: Mapped[dict[str, Any]]
    # The moment of the insert, not the transaction's start: two events from one transaction
    # keep their order.
    created_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
    published_at: Mapped[datetime | None]  # NULL until the broker confirmed every message
    retry_count: Mapped[int] = mapped_column(server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_error: Mapped[str | None]
    # Trace context, filled from M11.
    headers: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))


class ProcessedEvent(Base):
    """`consumer` has handled `event_id`. Written in the same transaction as the consumer's
    effect, so a message delivered again finds this row and does nothing."""

    __tablename__ = "processed_events"

    consumer: Mapped[str] = mapped_column(primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
