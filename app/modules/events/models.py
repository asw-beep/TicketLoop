"""Events, and the ticket providers who issue (and later transfer) their tickets."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, text
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


# The provider seeded by the M4 migration, so every database has one to attach events to.
FAKE_PROVIDER_CODE = "fakeprovider"


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
        # WHERE name ILIKE '%coldplay%'. A B-tree only helps when the start of the text is
        # known; trigrams (every run of three letters) find it anywhere. Needs pg_trgm.
        Index(
            "ix_events_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        # Search by city, ignoring case. Without it a city nobody is selling in ("Bangalore"
        # for Bengaluru) makes the sort index walk every ACTIVE listing looking for a match.
        Index("ix_events_city", text("lower(city)")),
        # GET /events: published events, soonest first.
        Index("ix_events_status_starts", "status", "starts_at", "id"),
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
