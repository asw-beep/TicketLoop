"""Tickets: one entitlement to attend one event.

The ticket row changes owner exactly once per sale, when the transfer completes (M12). It is
not inventory state: who may buy it is decided by the listing row (see listings/models.py).
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, Timestamps, UUIDPrimaryKey, one_of


class VerificationStatus(StrEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class TicketStatus(StrEnum):
    ACTIVE = "ACTIVE"  # a valid ticket
    VOID = "VOID"  # cancelled by the provider or found to be fake


class Ticket(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "tickets"
    __table_args__ = (
        # One real ticket (the provider's reference within an event) is registered once.
        UniqueConstraint(
            "event_id", "provider_reference", name="uq_tickets_event_provider_reference"
        ),
        CheckConstraint("original_price_paise > 0", name="price"),
        CheckConstraint(
            one_of("verification_status", VerificationStatus), name="verification_status"
        ),
        CheckConstraint(one_of("status", TicketStatus), name="status"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    original_owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    current_owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    ticket_category: Mapped[str]  # the provider's label: "GENERAL", "VIP", ...
    seat_details: Mapped[str | None]
    original_price_paise: Mapped[int] = mapped_column(BigInteger)
    purchase_reference: Mapped[str]  # private
    provider_reference: Mapped[str]  # private: the provider's id for this ticket
    verification_status: Mapped[str] = mapped_column(server_default=VerificationStatus.PENDING)
    transfer_eligible: Mapped[bool] = mapped_column(server_default=text("false"))
    status: Mapped[str] = mapped_column(server_default=TicketStatus.ACTIVE)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[datetime | None]
