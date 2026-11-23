"""The audit log: an append-only record of every important state change and admin action.

No foreign keys on purpose. A row can be about any kind of entity, and recording an action
must never fail because of a missing reference. A database trigger (see the first migration)
refuses UPDATE and DELETE, so history cannot be rewritten, even by a bug.
"""

import uuid
from typing import Any

from sqlalchemy import Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, CreatedAt, UUIDPrimaryKey


class AuditLog(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "audit_logs"
    # One entity's history, oldest first.
    __table_args__ = (Index("ix_audit_logs_entity", "entity_type", "entity_id", "created_at"),)

    actor_id: Mapped[uuid.UUID | None]  # NULL means the system: a worker or scheduled job
    entity_type: Mapped[str]  # "listing", "reservation", "order", ...
    entity_id: Mapped[uuid.UUID]
    action: Mapped[str]  # "listing.approved", "reservation.expired", ...
    request_id: Mapped[str | None]  # links this row to that request's log lines
    details: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
