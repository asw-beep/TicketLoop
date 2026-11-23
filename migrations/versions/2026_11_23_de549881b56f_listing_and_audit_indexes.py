"""M5: indexes for listings and the audit log.

Drafted with `alembic revision --autogenerate`; read and tidied by hand. Indexes only; the
tables, CHECKs and the one-open-listing-per-ticket index came with the first migration.

- ix_listings_review_queue: the admin review queue (PENDING_VERIFICATION, oldest first).
- ix_listings_active_expiry: expire_listings() finding ACTIVE listings past their expiry.
- ix_listings_seller: a seller's own listings, newest first.
- ix_listings_event: the event-cancel cascade and the expiry pull-back (Postgres does not
  index foreign keys).
- ix_audit_logs_entity: one entity's history, oldest first.

Revision ID: de549881b56f
Revises: 027244e97413
Create Date: 2026-11-23 13:58:25.819086
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "de549881b56f"
down_revision: str | Sequence[str] | None = "027244e97413"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_logs_entity",
        "audit_logs",
        ["entity_type", "entity_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_listings_active_expiry",
        "listings",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index("ix_listings_event", "listings", ["event_id"], unique=False)
    op.create_index(
        "ix_listings_review_queue",
        "listings",
        ["created_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING_VERIFICATION'"),
    )
    op.create_index(
        "ix_listings_seller", "listings", ["seller_id", "created_at", "id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_listings_seller", table_name="listings")
    op.drop_index(
        "ix_listings_review_queue",
        table_name="listings",
        postgresql_where=sa.text("status = 'PENDING_VERIFICATION'"),
    )
    op.drop_index("ix_listings_event", table_name="listings")
    op.drop_index(
        "ix_listings_active_expiry",
        table_name="listings",
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.drop_index("ix_audit_logs_entity", table_name="audit_logs")
