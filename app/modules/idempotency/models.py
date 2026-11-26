"""Idempotency keys: what we answered a request that may be sent again."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, CreatedAt


class IdempotencyKeyRow(CreatedAt, Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        CheckConstraint("length(key) BETWEEN 1 AND 255", name="length"),
        Index("ix_idempotency_keys_expiry", "expires_at"),
    )

    # One user's keys never meet another's.
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    request_method: Mapped[str]
    request_path: Mapped[str]
    request_hash: Mapped[str]
    response_status: Mapped[int | None] = mapped_column(SmallInteger)
    response_body: Mapped[dict[str, Any] | None]
    # Held while a request with this key is calling another service outside any transaction.
    locked_at: Mapped[datetime | None]
    expires_at: Mapped[datetime]
