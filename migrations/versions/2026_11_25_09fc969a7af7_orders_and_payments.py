"""M8: orders, idempotency keys and payments.

Drafted with `alembic revision --autogenerate`; read and tidied by hand.

- uq_orders_reservation: one order per reservation, whatever the Idempotency-Keys say.
- uq_payments_one_live_per_order: an order is being paid for at most once.
- uq_payment_events_provider_event: a webhook delivered again cannot insert.
- ix_payments_unresolved: the payments reconciliation asks the gateway about.
- ix_idempotency_keys_expiry: removing keys past their 24 hours.

Revision ID: 09fc969a7af7
Revises: c80056e86b71
Create Date: 2026-11-25 23:31:37.019226

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "09fc969a7af7"
down_revision: str | Sequence[str] | None = "c80056e86b71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("status", sa.Text(), server_default="RUNNING", nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("fixed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "findings", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED')",
            name=op.f("ck_reconciliation_runs_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reconciliation_runs")),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_method", sa.Text(), nullable=False),
        sa.Column("request_path", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("response_status", sa.SmallInteger(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(key) BETWEEN 1 AND 255", name=op.f("ck_idempotency_keys_length")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_idempotency_keys_user_id_users")
        ),
        sa.PrimaryKeyConstraint("user_id", "key", name=op.f("pk_idempotency_keys")),
    )
    op.create_index("ix_idempotency_keys_expiry", "idempotency_keys", ["expires_at"], unique=False)
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("buyer_id", sa.Uuid(), nullable=False),
        sa.Column("seller_id", sa.Uuid(), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), server_default="INR", nullable=False),
        sa.Column("status", sa.Text(), server_default="CREATED", nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("currency = 'INR'", name=op.f("ck_orders_currency")),
        sa.CheckConstraint(
            "status IN ('CREATED', 'PAYMENT_PENDING', 'PAID', 'PAYMENT_FAILED', 'CANCELLED', "
            "'TRANSFER_PENDING', 'COMPLETED', 'TRANSFER_FAILED')",
            name=op.f("ck_orders_status"),
        ),
        sa.CheckConstraint("amount_paise > 0", name=op.f("ck_orders_amount")),
        sa.CheckConstraint("buyer_id <> seller_id", name=op.f("ck_orders_not_own_listing")),
        sa.ForeignKeyConstraint(["buyer_id"], ["users.id"], name=op.f("fk_orders_buyer_id_users")),
        sa.ForeignKeyConstraint(
            ["listing_id"], ["listings.id"], name=op.f("fk_orders_listing_id_listings")
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["reservations.id"],
            name=op.f("fk_orders_reservation_id_reservations"),
        ),
        sa.ForeignKeyConstraint(
            ["seller_id"], ["users.id"], name=op.f("fk_orders_seller_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("reservation_id", name="uq_orders_reservation"),
    )
    op.create_index("ix_orders_buyer", "orders", ["buyer_id", "created_at", "id"], unique=False)
    op.create_index("ix_orders_listing", "orders", ["listing_id"], unique=False)
    op.create_index("ix_orders_seller", "orders", ["seller_id", "created_at", "id"], unique=False)
    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.Text(), server_default="fakepay", nullable=False),
        sa.Column("provider_transaction_id", sa.Text(), nullable=True),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), server_default="INR", nullable=False),
        sa.Column("status", sa.Text(), server_default="CREATED", nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("provider_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("currency = 'INR'", name=op.f("ck_payments_currency")),
        sa.CheckConstraint("provider IN ('fakepay')", name=op.f("ck_payments_provider")),
        sa.CheckConstraint(
            "status IN ('CREATED', 'PENDING', 'SUCCEEDED', 'FAILED')",
            name=op.f("ck_payments_status"),
        ),
        sa.CheckConstraint("amount_paise > 0", name=op.f("ck_payments_amount")),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_payments_order_id_orders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_payments_idempotency_key")),
        sa.UniqueConstraint(
            "provider", "provider_transaction_id", name="uq_payments_provider_transaction"
        ),
    )
    op.create_index("ix_payments_order", "payments", ["order_id"], unique=False)
    op.create_index(
        "ix_payments_unresolved",
        "payments",
        ["updated_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('CREATED', 'PENDING')"),
    )
    op.create_index(
        "uq_payments_one_live_per_order",
        "payments",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('CREATED', 'PENDING', 'SUCCEEDED')"),
    )
    op.create_table(
        "payment_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_event_id", sa.Text(), nullable=False),
        sa.Column("payment_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("processing_status", sa.Text(), server_default="RECEIVED", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "processing_status IN ('RECEIVED', 'PROCESSED', 'IGNORED', 'FAILED')",
            name=op.f("ck_payment_events_processing_status"),
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"], ["payments.id"], name=op.f("fk_payment_events_payment_id_payments")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_events")),
        sa.UniqueConstraint(
            "provider", "provider_event_id", name="uq_payment_events_provider_event"
        ),
    )
    op.create_index(
        "ix_payment_events_payment", "payment_events", ["payment_id", "received_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_payment_events_payment", table_name="payment_events")
    op.drop_table("payment_events")
    op.drop_index(
        "uq_payments_one_live_per_order",
        table_name="payments",
        postgresql_where=sa.text("status IN ('CREATED', 'PENDING', 'SUCCEEDED')"),
    )
    op.drop_index(
        "ix_payments_unresolved",
        table_name="payments",
        postgresql_where=sa.text("status IN ('CREATED', 'PENDING')"),
    )
    op.drop_index("ix_payments_order", table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_orders_seller", table_name="orders")
    op.drop_index("ix_orders_listing", table_name="orders")
    op.drop_index("ix_orders_buyer", table_name="orders")
    op.drop_table("orders")
    op.drop_index("ix_idempotency_keys_expiry", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_table("reconciliation_runs")
