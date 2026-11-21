"""Events, and the ticket providers who issue (and later transfer) their tickets."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, CreatedAt, Timestamps, UUIDPrimaryKey, one_of


class EventCategory(StrEnum):
    CONCERT = "CONCERT"
    SPORTS = "SPORTS"
    COLLEGE = "COLLEGE"
    OTHER = "OTHER"


class EventStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class TicketProvider(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "ticket_providers"
    __table_args__ = (UniqueConstraint("code"),)

    code: Mapped[str]  # "fakeprovider" now, a real provider later
    name: Mapped[str]


class Event(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(one_of("category", EventCategory), name="category"),
        CheckConstraint(one_of("status", EventStatus), name="status"),
    )

    name: Mapped[str]
    description: Mapped[str] = mapped_column(server_default="")
    category: Mapped[str]
    venue: Mapped[str]
    city: Mapped[str]
    starts_at: Mapped[datetime]
    organizer: Mapped[str]
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ticket_providers.id"))
    status: Mapped[str] = mapped_column(server_default=EventStatus.DRAFT)
