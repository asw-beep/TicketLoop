"""Listings: one ticket offered at one fixed price.

The listing row is the lock target. Reserving a ticket means moving exactly this row from
ACTIVE to RESERVED with a conditional UPDATE (M8); no other row decides who gets the ticket.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, Timestamps, UUIDPrimaryKey, one_of


class ListingStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    ACTIVE = "ACTIVE"
    RESERVED = "RESERVED"
    SOLD = "SOLD"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


# A listing in one of these still has a claim on its ticket.
OPEN_LISTING_STATUSES = (
    ListingStatus.DRAFT,
    ListingStatus.PENDING_VERIFICATION,
    ListingStatus.ACTIVE,
    ListingStatus.RESERVED,
)


class Listing(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "listings"
    __table_args__ = (
        CheckConstraint(one_of("status", ListingStatus), name="status"),
        # At least ₹1, and never above what the seller paid: resale at face value or less.
        CheckConstraint("asking_price_paise >= 100", name="min_price"),
        CheckConstraint("asking_price_paise <= original_price_paise", name="price_cap"),
        CheckConstraint(
            "status <> 'REJECTED' OR rejection_reason IS NOT NULL", name="rejection_reason"
        ),
        # FR-04 rule 2: at most one open listing per ticket. Partial, so a cancelled or sold
        # listing no longer counts and the ticket can be listed again.
        Index(
            "uq_listings_one_open_per_ticket",
            "ticket_id",
            unique=True,
            postgresql_where=text(one_of("status", OPEN_LISTING_STATUSES)),
        ),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"))
    # Copied from the ticket so discovery can filter by event without a join. Safe to copy:
    # a ticket never changes event.
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    asking_price_paise: Mapped[int] = mapped_column(BigInteger)
    # Copied from the ticket so the price-cap CHECK can compare both prices in one row.
    original_price_paise: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(server_default=ListingStatus.DRAFT)
    expires_at: Mapped[datetime]  # defaults to the event start (M5)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None]
    rejection_reason: Mapped[str | None]
