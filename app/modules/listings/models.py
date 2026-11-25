"""Listings: one ticket offered at one fixed price.

The listing row is the lock target. Reserving a ticket means moving exactly this row from
ACTIVE to RESERVED with a conditional UPDATE (M8); no other row decides who gets the ticket.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, Timestamps, UUIDPrimaryKey, one_of
from app.modules.events.models import Event
from app.modules.tickets.models import Ticket


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
        # The admin review queue, oldest first.
        Index(
            "ix_listings_review_queue",
            "created_at",
            "id",
            postgresql_where=text("status = 'PENDING_VERIFICATION'"),
        ),
        # expire_listings(): ACTIVE listings whose expiry has passed.
        Index(
            "ix_listings_active_expiry",
            "expires_at",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        # Search sorted by price, and by date (M6), in either direction: a B-tree reads
        # backwards just as well. Partial, because only ACTIVE listings are ever searched.
        Index(
            "ix_listings_active_by_price",
            "asking_price_paise",
            "id",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index(
            "ix_listings_active_by_created",
            "created_at",
            "id",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        # A seller's own listings, newest first.
        Index("ix_listings_seller", "seller_id", "created_at", "id"),
        # The event-cancel cascade and the expiry pull-back. Postgres does not index foreign keys.
        Index("ix_listings_event", "event_id"),
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

    # Joined eagerly, so a listing always arrives with what its responses show. A SELECT ...
    # FOR UPDATE on listings must say `with_for_update(of=Listing)`, or it locks these rows too.
    ticket: Mapped[Ticket] = relationship(lazy="joined", innerjoin=True)
    event: Mapped[Event] = relationship(lazy="joined", innerjoin=True)

    @property
    def ticket_category(self) -> str:
        return self.ticket.ticket_category

    @property
    def seat_details(self) -> str | None:
        return self.ticket.seat_details
