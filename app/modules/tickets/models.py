"""Tickets: one entitlement to attend one event.

The ticket row changes owner exactly once per sale, when the transfer completes (M12). It is
not inventory state: who may buy it is decided by the listing row (see listings/models.py).
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, CreatedAt, Timestamps, UUIDPrimaryKey, one_of
from app.modules.events.models import Event

# Evidence uploads (M4): at most 5 MiB each, at most 5 per ticket.
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024  # 5_242_880
MAX_DOCUMENTS_PER_TICKET = 5


class VerificationStatus(StrEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class DocumentType(StrEnum):
    """The only file types accepted as evidence, decided from a file's bytes, never its name."""

    PDF = "application/pdf"
    PNG = "image/png"
    JPEG = "image/jpeg"


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
    verification_note: Mapped[str | None]  # the admin's reason, shown to the owner

    # Loaded with the ticket in the same query: every ticket response shows its event.
    event: Mapped[Event] = relationship(lazy="joined", innerjoin=True)


class TicketDocument(UUIDPrimaryKey, CreatedAt, Base):
    """A pointer to one private evidence file in object storage. The file itself is never in
    the database, and its key is random: the uploader's filename is not kept anywhere."""

    __tablename__ = "ticket_documents"
    __table_args__ = (
        UniqueConstraint("storage_key"),
        CheckConstraint(one_of("content_type", DocumentType), name="type"),
        CheckConstraint(f"size_bytes BETWEEN 1 AND {MAX_DOCUMENT_BYTES}", name="size"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    storage_key: Mapped[str]
    content_type: Mapped[str]
    size_bytes: Mapped[int] = mapped_column(BigInteger)
