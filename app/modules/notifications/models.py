"""In-app notifications: one row per person per event that concerns them."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, UUIDPrimaryKey


class Notification(UUIDPrimaryKey, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # One per person per event, however many times the event is delivered.
        UniqueConstraint("source_event_id", "user_id", name="uq_notifications_event_user"),
        # A person's notifications, newest first.
        Index("ix_notifications_user", "user_id", "created_at", "id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    notification_type: Mapped[str]  # 'payment_succeeded', 'ticket_sold', ...
    payload: Mapped[dict[str, Any]]  # ids to link to; never another person's identity
    source_event_id: Mapped[uuid.UUID]  # the outbox event that caused it
    read_at: Mapped[datetime | None]
    # The moment of the insert: two written in one transaction keep their order.
    created_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
