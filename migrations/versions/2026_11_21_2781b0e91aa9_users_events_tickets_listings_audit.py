"""M2: users, ticket providers, events, tickets, listings and the audit log.

Drafted with `alembic revision --autogenerate`, then finished by hand:
- tables ordered so every foreign key points at a table created before it;
- the append-only trigger on audit_logs, which autogenerate cannot see because triggers and
  functions are not part of the SQLAlchemy models.

Autogenerate does include CHECK constraints and partial indexes when it creates a table, but
it cannot detect a later change to a CHECK. Those changes are always written by hand.

Revision ID: 2781b0e91aa9
Revises:
Create Date: 2026-11-21 09:18:34.440248
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "2781b0e91aa9"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LISTING_STATUSES = (
    "'DRAFT', 'PENDING_VERIFICATION', 'ACTIVE', 'RESERVED', 'SOLD', 'REJECTED', 'CANCELLED', "
    "'EXPIRED'"
)
OPEN_LISTING_STATUSES = "'DRAFT', 'PENDING_VERIFICATION', 'ACTIVE', 'RESERVED'"


def id_column() -> sa.Column:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def timestamp_column(name: str) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "users",
        id_column(),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), server_default="USER", nullable=False),
        sa.Column("account_status", sa.Text(), server_default="ACTIVE", nullable=False),
        timestamp_column("created_at"),
        timestamp_column("updated_at"),
        sa.CheckConstraint(
            "account_status IN ('ACTIVE', 'SUSPENDED')", name=op.f("ck_users_account_status")
        ),
        sa.CheckConstraint("role IN ('USER', 'ADMIN')", name=op.f("ck_users_role")),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )

    op.create_table(
        "ticket_providers",
        id_column(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        timestamp_column("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_providers")),
        sa.UniqueConstraint("code", name=op.f("uq_ticket_providers_code")),
    )

    op.create_table(
        "events",
        id_column(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("venue", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organizer", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), server_default="DRAFT", nullable=False),
        timestamp_column("created_at"),
        timestamp_column("updated_at"),
        sa.CheckConstraint(
            "category IN ('CONCERT', 'SPORTS', 'COLLEGE', 'OTHER')",
            name=op.f("ck_events_category"),
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'CANCELLED', 'COMPLETED')",
            name=op.f("ck_events_status"),
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["ticket_providers.id"],
            name=op.f("fk_events_provider_id_ticket_providers"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )

    op.create_table(
        "tickets",
        id_column(),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("original_owner_id", sa.Uuid(), nullable=False),
        sa.Column("current_owner_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_category", sa.Text(), nullable=False),
        sa.Column("seat_details", sa.Text(), nullable=True),
        sa.Column("original_price_paise", sa.BigInteger(), nullable=False),
        sa.Column("purchase_reference", sa.Text(), nullable=False),
        sa.Column("provider_reference", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.Text(), server_default="PENDING", nullable=False),
        sa.Column(
            "transfer_eligible", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("status", sa.Text(), server_default="ACTIVE", nullable=False),
        sa.Column("verified_by", sa.Uuid(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        timestamp_column("created_at"),
        timestamp_column("updated_at"),
        sa.CheckConstraint("status IN ('ACTIVE', 'VOID')", name=op.f("ck_tickets_status")),
        sa.CheckConstraint(
            "verification_status IN ('PENDING', 'VERIFIED', 'REJECTED')",
            name=op.f("ck_tickets_verification_status"),
        ),
        sa.CheckConstraint("original_price_paise > 0", name=op.f("ck_tickets_price")),
        sa.ForeignKeyConstraint(
            ["current_owner_id"], ["users.id"], name=op.f("fk_tickets_current_owner_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], name=op.f("fk_tickets_event_id_events")
        ),
        sa.ForeignKeyConstraint(
            ["original_owner_id"], ["users.id"], name=op.f("fk_tickets_original_owner_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["verified_by"], ["users.id"], name=op.f("fk_tickets_verified_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
        sa.UniqueConstraint(
            "event_id", "provider_reference", name="uq_tickets_event_provider_reference"
        ),
    )

    op.create_table(
        "listings",
        id_column(),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("seller_id", sa.Uuid(), nullable=False),
        sa.Column("asking_price_paise", sa.BigInteger(), nullable=False),
        sa.Column("original_price_paise", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), server_default="DRAFT", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        timestamp_column("created_at"),
        timestamp_column("updated_at"),
        sa.CheckConstraint(
            "status <> 'REJECTED' OR rejection_reason IS NOT NULL",
            name=op.f("ck_listings_rejection_reason"),
        ),
        sa.CheckConstraint(f"status IN ({LISTING_STATUSES})", name=op.f("ck_listings_status")),
        sa.CheckConstraint(
            "asking_price_paise <= original_price_paise", name=op.f("ck_listings_price_cap")
        ),
        sa.CheckConstraint("asking_price_paise >= 100", name=op.f("ck_listings_min_price")),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], name=op.f("fk_listings_event_id_events")
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["users.id"], name=op.f("fk_listings_reviewed_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["seller_id"], ["users.id"], name=op.f("fk_listings_seller_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_listings_ticket_id_tickets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_listings")),
    )
    # FR-04 rule 2: at most one open listing per ticket.
    op.create_index(
        "uq_listings_one_open_per_ticket",
        "listings",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({OPEN_LISTING_STATUSES})"),
    )

    op.create_table(
        "audit_logs",
        id_column(),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        timestamp_column("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )

    # Hand-written: history tables are append-only. The app never updates or deletes them,
    # and this trigger makes the database refuse even if a bug or a person tries. M13 attaches
    # the same function to dispute_events.
    op.execute(
        """
        CREATE FUNCTION forbid_update_delete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_append_only
            BEFORE UPDATE OR DELETE ON audit_logs
            FOR EACH ROW EXECUTE FUNCTION forbid_update_delete()
        """
    )


def downgrade() -> None:
    # Reverse order: dependents first, so no foreign key or trigger is left dangling.
    op.execute("DROP TRIGGER audit_logs_append_only ON audit_logs")
    op.execute("DROP FUNCTION forbid_update_delete()")
    op.drop_table("audit_logs")
    op.drop_index("uq_listings_one_open_per_ticket", table_name="listings")
    op.drop_table("listings")
    op.drop_table("tickets")
    op.drop_table("events")
    op.drop_table("ticket_providers")
    op.drop_table("users")
