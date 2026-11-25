"""Reservations: one buyer's 10-minute hold on one listing.

The listing row decides who gets the hold (its ACTIVE -> RESERVED update). This table records
whose it is and until when. Its partial unique index is the safety net: whatever the code does,
Postgres will not store two live reservations for one listing.
"""

import uuid
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, Timestamps, UUIDPrimaryKey, one_of
from app.modules.listings.models import Listing

HOLD = timedelta(minutes=10)


class ReservationStatus(StrEnum):
    CREATED = "CREATED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    CONFIRMED = "CONFIRMED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# A reservation in one of these still has a claim on its listing.
LIVE_STATUSES = (
    ReservationStatus.CREATED,
    ReservationStatus.PAYMENT_PENDING,
    ReservationStatus.CONFIRMED,
)
# Not paid for yet: the buyer still has something to do, or a payment is in flight.
UNRESOLVED_STATUSES = (ReservationStatus.CREATED, ReservationStatus.PAYMENT_PENDING)


class Reservation(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint(one_of("status", ReservationStatus), name="status"),
        # The safety net under the listing's conditional UPDATE.
        Index(
            "uq_reservations_one_live_per_listing",
            "listing_id",
            unique=True,
            postgresql_where=text(one_of("status", LIVE_STATUSES)),
        ),
        # The expiry scan (CREATED), and M8's look at payments that never resolved.
        Index(
            "ix_reservations_due",
            "expires_at",
            postgresql_where=text(one_of("status", UNRESOLVED_STATUSES)),
        ),
        # One buyer's reservations.
        Index("ix_reservations_buyer", "buyer_id", "created_at", "id"),
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id"))
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(server_default=ReservationStatus.CREATED)
    expires_at: Mapped[datetime]

    # Joined eagerly, like a listing's ticket and event. A SELECT ... FOR UPDATE here must say
    # `with_for_update(of=Reservation)`.
    listing: Mapped[Listing] = relationship(lazy="joined", innerjoin=True)
