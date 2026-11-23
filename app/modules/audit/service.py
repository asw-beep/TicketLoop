"""Writing audit rows.

Every important state change calls `record()` inside the same transaction as the change, so
the row commits with the change or rolls back with it. There is never one without the other.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog


def record(
    db: Session,
    *,
    actor_id: uuid.UUID | None,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    details: Mapping[str, Any] | None = None,
) -> AuditLog:
    """Add one audit row to the current transaction and flush it.

    Never commits: the caller's transaction decides whether the row survives. Flushing makes a
    database error surface here, at the call site, rather than at commit.

    `actor_id` is the user who acted, or None for the system (a script or scheduled job). The
    request ID comes from the log context the request middleware binds, so it is None outside
    a request. `details` must never hold secrets or private data: no tokens, presigned URLs,
    purchase or provider references, or email addresses.

    `created_at` is the moment of this write (`clock_timestamp()`), not the column default
    `now()`, which is the start of the transaction: rows written in one transaction would
    otherwise share a timestamp and a history read back in time order could shuffle them.
    """
    row = AuditLog(
        created_at=func.clock_timestamp(),
        actor_id=actor_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        request_id=structlog.contextvars.get_contextvars().get("request_id"),
        details={key: _json_safe(value) for key, value in (details or {}).items()},
    )
    db.add(row)
    db.flush()
    return row


def _json_safe(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value
