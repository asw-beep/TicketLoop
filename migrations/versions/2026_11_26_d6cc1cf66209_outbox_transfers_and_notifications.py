"""M9: the outbox, transfers and notifications.

Drafted with `alembic revision --autogenerate`; read and tidied by hand.

- ix_outbox_events_unpublished: the relay's question, what is due and not yet published.
- processed_events (consumer, event_id): a message delivered again finds its row and does
  nothing.
- uq_transfers_order: a duplicate payment.succeeded cannot start a second transfer.
- ix_transfers_review_queue: the transfers that need a person.
- uq_notifications_event_user: one notification per person per event.

Revision ID: d6cc1cf66209
Revises: 09fc969a7af7
Create Date: 2026-11-26 09:30:29.928159

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d6cc1cf66209"
down_revision: str | Sequence[str] | None = "09fc969a7af7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("aggregate_type", sa.Text(), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "headers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint("retry_count >= 0", name=op.f("ck_outbox_events_retry_count")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
    )
    op.create_index(
        "ix_outbox_events_unpublished",
        "outbox_events",
        ["next_attempt_at", "created_at"],
        unique=False,
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_table(
        "processed_events",
        sa.Column("consumer", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("consumer", "event_id", name=op.f("pk_processed_events")),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("notification_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_notifications_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint("source_event_id", "user_id", name="uq_notifications_event_user"),
    )
    op.create_index(
        "ix_notifications_user", "notifications", ["user_id", "created_at", "id"], unique=False
    )
    op.create_table(
        "transfers",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("from_user_id", sa.Uuid(), nullable=False),
        sa.Column("to_user_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("provider_transfer_reference", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="INITIATED", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('INITIATED', 'PROCESSING', 'COMPLETED', 'REQUIRES_MANUAL_REVIEW')",
            name=op.f("ck_transfers_status"),
        ),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_transfers_attempt_count")),
        sa.CheckConstraint("from_user_id <> to_user_id", name=op.f("ck_transfers_not_to_self")),
        sa.ForeignKeyConstraint(
            ["from_user_id"], ["users.id"], name=op.f("fk_transfers_from_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_transfers_order_id_orders")
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["ticket_providers.id"],
            name=op.f("fk_transfers_provider_id_ticket_providers"),
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_transfers_ticket_id_tickets")
        ),
        sa.ForeignKeyConstraint(
            ["to_user_id"], ["users.id"], name=op.f("fk_transfers_to_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transfers")),
        sa.UniqueConstraint("order_id", name="uq_transfers_order"),
        sa.UniqueConstraint("provider_transfer_reference", name="uq_transfers_provider_reference"),
    )
    op.create_index(
        "ix_transfers_review_queue",
        "transfers",
        ["updated_at"],
        unique=False,
        postgresql_where=sa.text("status = 'REQUIRES_MANUAL_REVIEW'"),
    )
    op.create_index("ix_transfers_ticket", "transfers", ["ticket_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_transfers_ticket", table_name="transfers")
    op.drop_index(
        "ix_transfers_review_queue",
        table_name="transfers",
        postgresql_where=sa.text("status = 'REQUIRES_MANUAL_REVIEW'"),
    )
    op.drop_table("transfers")
    op.drop_index("ix_notifications_user", table_name="notifications")
    op.drop_table("notifications")
    op.drop_table("processed_events")
    op.drop_index(
        "ix_outbox_events_unpublished",
        table_name="outbox_events",
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.drop_table("outbox_events")
