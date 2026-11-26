"""Transfers: moving one ticket from its seller to its buyer, once per paid order."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, Timestamps, UUIDPrimaryKey, one_of
from app.modules.orders.models import Order

# Retryable failures are tried this many times in all, then the transfer goes to review.
MAX_ATTEMPTS = 5


class TransferStatus(StrEnum):
    INITIATED = "INITIATED"  # the order is paid; nobody has asked the provider yet
    PROCESSING = "PROCESSING"  # asked, and the answer is not in
    COMPLETED = "COMPLETED"
    REQUIRES_MANUAL_REVIEW = "REQUIRES_MANUAL_REVIEW"  # where a failed transfer ends


FINAL_TRANSFER_STATUSES = (TransferStatus.COMPLETED, TransferStatus.REQUIRES_MANUAL_REVIEW)


class Transfer(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint(one_of("status", TransferStatus), name="status"),
        CheckConstraint("attempt_count >= 0", name="attempt_count"),
        CheckConstraint("from_user_id <> to_user_id", name="not_to_self"),
        # A duplicate payment.succeeded cannot start a second transfer.
        UniqueConstraint("order_id", name="uq_transfers_order"),
        UniqueConstraint("provider_transfer_reference", name="uq_transfers_provider_reference"),
        # What an operator would look at: the transfers that need a person.
        Index(
            "ix_transfers_review_queue",
            "updated_at",
            postgresql_where=text("status = 'REQUIRES_MANUAL_REVIEW'"),
        ),
        Index("ix_transfers_ticket", "ticket_id"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"))
    from_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    to_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ticket_providers.id"))
    provider_transfer_reference: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default=TransferStatus.INITIATED)
    attempt_count: Mapped[int] = mapped_column(server_default="0")
    last_error: Mapped[str | None]  # for admins and logs, never shown to buyer or seller
    completed_at: Mapped[datetime | None]


# An order's transfer, if it has one, for the order's page. Here and not in orders/models.py,
# which must not know about transfers.
Order.transfer = relationship(Transfer, uselist=False, viewonly=True, lazy="selectin")
