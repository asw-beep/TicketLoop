"""M7: reservations.

Drafted with `alembic revision --autogenerate`; read and tidied by hand.

- uq_reservations_one_live_per_listing: the safety net under the listing's conditional UPDATE.
  It also serves the lookup of the live reservation of one listing.
- ix_reservations_due: the expiry loop, and M8's look at payments that never resolved.
- ix_reservations_buyer: one buyer's reservations.

Revision ID: c80056e86b71
Revises: 1e89a2dcf2cc
Create Date: 2026-11-25 22:34:56.984236

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c80056e86b71"
down_revision: str | Sequence[str] | None = "1e89a2dcf2cc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reservations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("buyer_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), server_default="CREATED", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            "status IN ('CREATED', 'PAYMENT_PENDING', 'CONFIRMED', 'EXPIRED', 'FAILED', "
            "'CANCELLED')",
            name=op.f("ck_reservations_status"),
        ),
        sa.ForeignKeyConstraint(
            ["buyer_id"], ["users.id"], name=op.f("fk_reservations_buyer_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"], ["listings.id"], name=op.f("fk_reservations_listing_id_listings")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reservations")),
    )
    op.create_index(
        "ix_reservations_buyer", "reservations", ["buyer_id", "created_at", "id"], unique=False
    )
    op.create_index(
        "ix_reservations_due",
        "reservations",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('CREATED', 'PAYMENT_PENDING')"),
    )
    op.create_index(
        "uq_reservations_one_live_per_listing",
        "reservations",
        ["listing_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('CREATED', 'PAYMENT_PENDING', 'CONFIRMED')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_reservations_one_live_per_listing",
        table_name="reservations",
        postgresql_where=sa.text("status IN ('CREATED', 'PAYMENT_PENDING', 'CONFIRMED')"),
    )
    op.drop_index(
        "ix_reservations_due",
        table_name="reservations",
        postgresql_where=sa.text("status IN ('CREATED', 'PAYMENT_PENDING')"),
    )
    op.drop_index("ix_reservations_buyer", table_name="reservations")
    op.drop_table("reservations")
