"""M3: refresh tokens and password-reset tokens.

Drafted with `alembic revision --autogenerate`; read and tidied by hand. Nothing here is
invisible to autogenerate (no triggers or functions), so the draft was complete.

Both tables hold a SHA-256 of each token, never the token. `refresh_tokens.family_id` groups
every token descended from one login, so reuse of a revoked token can revoke the lot.

Revision ID: 544b66192ea4
Revises: 2781b0e91aa9
Create Date: 2026-11-22 15:05:46.039704
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "544b66192ea4"
down_revision: str | Sequence[str] | None = "2781b0e91aa9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def id_column() -> sa.Column:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def created_at_column() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        id_column(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by", sa.Uuid(), nullable=True),
        created_at_column(),
        sa.CheckConstraint(
            "replaced_by IS NULL OR revoked_at IS NOT NULL",
            name=op.f("ck_refresh_tokens_replaced_means_revoked"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_refresh_tokens_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by"],
            ["refresh_tokens.id"],
            name=op.f("fk_refresh_tokens_replaced_by_refresh_tokens"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(op.f("ix_refresh_tokens_family_id"), "refresh_tokens", ["family_id"])
    # Partial: only live tokens, which is all "revoke every session of this user" looks for.
    op.create_index(
        "ix_refresh_tokens_user_id_live",
        "refresh_tokens",
        ["user_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "password_reset_tokens",
        id_column(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        created_at_column(),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_password_reset_tokens_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_reset_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_password_reset_tokens_token_hash")),
    )


def downgrade() -> None:
    op.drop_table("password_reset_tokens")
    op.drop_index("ix_refresh_tokens_user_id_live", table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_family_id"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
