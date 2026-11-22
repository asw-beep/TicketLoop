"""M4: ticket documents, the verification note, and the seeded ticket provider.

Drafted with `alembic revision --autogenerate`; read and tidied by hand. Autogenerate cannot
see data, so the provider seed at the end of upgrade() and its removal were written by hand.

`ticket_documents` points at private evidence files in object storage. The key is random and
unique; the uploader's filename is never stored. The CHECKs repeat the upload rules (PDF, PNG
or JPEG; 1 byte to 5 MiB) so a bug in the API cannot store anything else.

`tickets.verification_note` is the admin's reason for a decision, shown to the owner.

Revision ID: 027244e97413
Revises: 544b66192ea4
Create Date: 2026-11-22 20:12:37.426541
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "027244e97413"
down_revision: str | Sequence[str] | None = "544b66192ea4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ticket_documents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_type IN ('application/pdf', 'image/png', 'image/jpeg')",
            name=op.f("ck_ticket_documents_type"),
        ),
        sa.CheckConstraint(
            "size_bytes BETWEEN 1 AND 5242880", name=op.f("ck_ticket_documents_size")
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_ticket_documents_ticket_id_tickets")
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"], name=op.f("fk_ticket_documents_uploaded_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_documents")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_ticket_documents_storage_key")),
    )
    op.create_index(
        op.f("ix_ticket_documents_ticket_id"), "ticket_documents", ["ticket_id"], unique=False
    )
    op.add_column("tickets", sa.Column("verification_note", sa.Text(), nullable=True))

    # Every event needs a ticket provider. Seed the stand-in one, so a fresh database (and
    # every test database, built from these migrations) can have events at once. The
    # downgrade keeps the row while events use it, so it may already be here on the way up.
    op.execute(
        "INSERT INTO ticket_providers (code, name) VALUES ('fakeprovider', 'FakeProvider') "
        "ON CONFLICT (code) DO NOTHING"
    )


def downgrade() -> None:
    # Remove the seeded provider only if no event uses it; otherwise keep it (and its events).
    op.execute(
        "DELETE FROM ticket_providers WHERE code = 'fakeprovider' "
        "AND NOT EXISTS (SELECT 1 FROM events WHERE events.provider_id = ticket_providers.id)"
    )
    op.drop_column("tickets", "verification_note")
    op.drop_index(op.f("ix_ticket_documents_ticket_id"), table_name="ticket_documents")
    op.drop_table("ticket_documents")
