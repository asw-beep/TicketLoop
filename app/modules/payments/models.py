"""Payments: attempts to collect an order's money, every webhook about them, and every
reconciliation run."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import CHAR, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, Timestamps, UUIDPrimaryKey, one_of
from app.modules.orders.models import CURRENCY, Order

PROVIDER = "fakepay"


class PaymentStatus(StrEnum):
    CREATED = "CREATED"  # saved, and the gateway perhaps asked
    PENDING = "PENDING"  # the gateway has an intent
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


# Not settled: money may be moving.
OPEN_PAYMENT_STATUSES = (PaymentStatus.CREATED, PaymentStatus.PENDING)


class Payment(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(one_of("status", PaymentStatus), name="status"),
        CheckConstraint(one_of("provider", (PROVIDER,)), name="provider"),
        CheckConstraint("amount_paise > 0", name="amount"),
        CheckConstraint(f"currency = '{CURRENCY}'", name="currency"),
        # NULLs count as distinct, so many rows may still wait for a transaction id.
        UniqueConstraint(
            "provider", "provider_transaction_id", name="uq_payments_provider_transaction"
        ),
        UniqueConstraint("idempotency_key"),
        # FR-09 rule 4: an order has one live payment.
        Index(
            "uq_payments_one_live_per_order",
            "order_id",
            unique=True,
            postgresql_where=text(
                one_of("status", (*OPEN_PAYMENT_STATUSES, PaymentStatus.SUCCEEDED))
            ),
        ),
        Index("ix_payments_order", "order_id"),
        # Reconciliation: open payments nothing has touched for a while.
        Index(
            "ix_payments_unresolved",
            "updated_at",
            postgresql_where=text(one_of("status", OPEN_PAYMENT_STATUSES)),
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    provider: Mapped[str] = mapped_column(server_default=PROVIDER)
    provider_transaction_id: Mapped[str | None]
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=CURRENCY)
    status: Mapped[str] = mapped_column(server_default=PaymentStatus.CREATED)
    # What we send the gateway: this payment's own id, so a repeated call is the same intent.
    idempotency_key: Mapped[str]
    # The gateway's latest words. The full history is payment_events.
    provider_response: Mapped[dict[str, Any] | None]
    failure_reason: Mapped[str | None]

    order: Mapped[Order] = relationship(lazy="joined", innerjoin=True)


class EventOutcome(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"  # it changed something
    IGNORED = "IGNORED"  # nothing left to change
    FAILED = "FAILED"  # someone should look: `error` says why


class PaymentEvent(UUIDPrimaryKey, Base):
    """One verified webhook. Never updated except to say what came of it, never deleted."""

    __tablename__ = "payment_events"
    __table_args__ = (
        CheckConstraint(one_of("processing_status", EventOutcome), name="processing_status"),
        # Webhook dedupe: the second delivery of an event cannot insert.
        UniqueConstraint("provider", "provider_event_id", name="uq_payment_events_provider_event"),
        Index("ix_payment_events_payment", "payment_id", "received_at"),
    )

    provider: Mapped[str]
    provider_event_id: Mapped[str]
    payment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payments.id"))
    event_type: Mapped[str]
    payload: Mapped[dict[str, Any]]
    processing_status: Mapped[str] = mapped_column(server_default=EventOutcome.RECEIVED)
    error: Mapped[str | None]
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())
    processed_at: Mapped[datetime | None]


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ReconciliationRun(UUIDPrimaryKey, Base):
    __tablename__ = "reconciliation_runs"
    __table_args__ = (CheckConstraint(one_of("status", RunStatus), name="status"),)

    status: Mapped[str] = mapped_column(server_default=RunStatus.RUNNING)
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    checked_count: Mapped[int] = mapped_column(server_default="0")
    fixed_count: Mapped[int] = mapped_column(server_default="0")
    # One entry per mismatch: what we had, what the gateway said, what was done.
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    error: Mapped[str | None]
