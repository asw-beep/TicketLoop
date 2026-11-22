"""Users, and the two kinds of opaque token they hold: refresh tokens and password-reset tokens.

Both token tables store a SHA-256 of the token, never the token. Someone who reads a backup
or the live table holds hashes they cannot turn back into working tokens.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import (
    Base,
    CreatedAt,
    Timestamps,
    UUIDPrimaryKey,
    one_of,
)


class UserRole(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email"),
        # Stops "A@x.in" and "a@x.in" from becoming two accounts.
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint(one_of("role", UserRole), name="role"),
        CheckConstraint(one_of("account_status", AccountStatus), name="account_status"),
    )

    email: Mapped[str]
    password_hash: Mapped[str]  # Argon2id PHC string; never the password itself
    full_name: Mapped[str]
    role: Mapped[str] = mapped_column(server_default=UserRole.USER)
    account_status: Mapped[str] = mapped_column(server_default=AccountStatus.ACTIVE)


class RefreshToken(UUIDPrimaryKey, CreatedAt, Base):
    """One row per refresh token ever issued.

    A login starts a family. Every refresh revokes the presented token and issues its
    successor in the same family, linked by `replaced_by`. If a revoked token comes back,
    two parties hold tokens from one family, so the whole family is revoked.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        # Rotation always revokes the token it replaces.
        CheckConstraint(
            "replaced_by IS NULL OR revoked_at IS NOT NULL", name="replaced_means_revoked"
        ),
        # Serves "revoke every live token this user has" (password change, suspension).
        Index(
            "ix_refresh_tokens_user_id_live", "user_id", postgresql_where=text("revoked_at IS NULL")
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    # Not a foreign key: a family is only a shared id, there is no families table.
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    token_hash: Mapped[str]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("refresh_tokens.id"))


class PasswordResetToken(UUIDPrimaryKey, CreatedAt, Base):
    """Single use: `used_at` is set by the same UPDATE that accepts the token."""

    __tablename__ = "password_reset_tokens"
    __table_args__ = (UniqueConstraint("token_hash"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str]
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
