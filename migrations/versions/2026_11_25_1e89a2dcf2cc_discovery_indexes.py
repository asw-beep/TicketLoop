"""M6: search indexes.

Drafted with `alembic revision --autogenerate`; read and tidied by hand. Autogenerate does not
see extensions, so pg_trgm is written here. It is a trusted extension: the database owner may
create it without being a superuser.

- ix_listings_active_by_price, ix_listings_active_by_created: search, one per sort.
- ix_events_name_trgm: `name ILIKE '%text%'`.
- ix_events_status_starts: published events, soonest first.
- ix_events_city: search by city, ignoring case.
- ix_tickets_category: search by ticket category, ignoring case.
- ix_tickets_owner: one person's tickets, newest first.

Revision ID: 1e89a2dcf2cc
Revises: de549881b56f
Create Date: 2026-11-25 21:54:23.203118

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1e89a2dcf2cc"
down_revision: str | Sequence[str] | None = "de549881b56f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_events_name_trgm",
        "events",
        ["name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.create_index("ix_events_city", "events", [sa.literal_column("lower(city)")], unique=False)
    op.create_index(
        "ix_events_status_starts", "events", ["status", "starts_at", "id"], unique=False
    )
    op.create_index(
        "ix_listings_active_by_created",
        "listings",
        ["created_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_listings_active_by_price",
        "listings",
        ["asking_price_paise", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_tickets_category",
        "tickets",
        [sa.literal_column("lower(ticket_category)")],
        unique=False,
    )
    op.create_index(
        "ix_tickets_owner", "tickets", ["current_owner_id", "created_at", "id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_tickets_owner", table_name="tickets")
    op.drop_index("ix_tickets_category", table_name="tickets")
    op.drop_index(
        "ix_listings_active_by_price",
        table_name="listings",
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.drop_index(
        "ix_listings_active_by_created",
        table_name="listings",
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.drop_index("ix_events_status_starts", table_name="events")
    op.drop_index("ix_events_city", table_name="events")
    op.drop_index(
        "ix_events_name_trgm",
        table_name="events",
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
