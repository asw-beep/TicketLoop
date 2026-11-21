"""The users table. M3 builds registration, login and tokens on top of it."""

from enum import StrEnum

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, Timestamps, UUIDPrimaryKey, one_of


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
    password_hash: Mapped[str]  # Argon2id from M3; never the password itself
    full_name: Mapped[str]
    role: Mapped[str] = mapped_column(server_default=UserRole.USER)
    account_status: Mapped[str] = mapped_column(server_default=AccountStatus.ACTIVE)
