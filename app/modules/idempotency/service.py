"""Idempotency keys (hard problem 2, after Brandur Leach's "Implementing Stripe-like Idempotency
Keys in Postgres"). Functions take the request's Session and never commit.

    asked = Asked.of(user, key, request, body)
    stored = claim(db, asked)           # the answer we already gave, or None: do the work
    ...the work...
    store(db, asked, 201, out)          # in the same transaction as the work

The key row, the work and the stored answer commit together or not at all. A second request
with the same key, sent while the first is running, waits on the unique index at claim() until
the first commits, then reads its answer; if the first rolled back, the second does the work.

A request that must call another service cannot hold its transaction open meanwhile. It claims
with lock=True and commits, calls, then stores the answer; until then others get a 409. A lock
older than LOCK_TIMEOUT belongs to a process that died, and the next request takes it over.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Self

from fastapi import Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.idempotency.errors import (
    IdempotencyKeyInProgressError,
    IdempotencyKeyReusedError,
)
from app.modules.idempotency.models import IdempotencyKeyRow as Row
from app.modules.identity.models import User

LIFETIME = timedelta(hours=24)
LOCK_TIMEOUT = timedelta(minutes=2)
REPLAYED_HEADER = "Idempotent-Replayed"


@dataclass(frozen=True)
class Asked:
    """Who asked for what, under which key."""

    user_id: uuid.UUID
    key: str
    method: str
    path: str
    request_hash: str

    @classmethod
    def of(cls, user: User, key: str, request: Request, body: BaseModel) -> Self:
        method, path = request.method, request.url.path
        # The validated body, so a client that spaces its JSON differently on a retry is still
        # sending the same request.
        canonical = json.dumps(
            body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        digest = hashlib.sha256(f"{method} {path}\n{canonical}".encode()).hexdigest()
        return cls(user.id, key, method, path, digest)


@dataclass(frozen=True)
class Stored:
    status: int
    body: dict[str, Any]


def _mine(asked: Asked) -> tuple[Any, ...]:
    return (Row.user_id == asked.user_id, Row.key == asked.key)


def claim(db: Session, asked: Asked, *, lock: bool = False) -> Stored | None:
    """The stored answer to this very request, or None: the caller now does the work."""
    db.execute(delete(Row).where(*_mine(asked), Row.expires_at <= func.now()))
    new = db.execute(
        insert(Row)
        .values(
            user_id=asked.user_id,
            key=asked.key,
            request_method=asked.method,
            request_path=asked.path,
            request_hash=asked.request_hash,
            locked_at=func.now() if lock else None,
            expires_at=func.now() + LIFETIME,
        )
        .on_conflict_do_nothing()
        .returning(Row.key)
    ).scalar_one_or_none()
    if new is not None:
        return None

    row = db.execute(
        select(Row.request_hash, Row.response_status, Row.response_body).where(*_mine(asked))
    ).one_or_none()
    if row is None:  # it expired and was removed this very moment
        raise IdempotencyKeyInProgressError()
    if row.request_hash != asked.request_hash:
        raise IdempotencyKeyReusedError()
    if row.response_status is not None:
        return Stored(row.response_status, row.response_body)

    # Claimed before and never answered: that request failed and let go, or its process died.
    if lock and _take_over(db, asked):
        return None
    raise IdempotencyKeyInProgressError()


def _take_over(db: Session, asked: Asked) -> bool:
    """One UPDATE, so of two retries only one gets it. clock_timestamp(): the lock was set by
    another transaction, perhaps while this one waited."""
    stale = func.clock_timestamp() - LOCK_TIMEOUT
    taken = db.execute(
        update(Row)
        .where(
            *_mine(asked),
            Row.response_status.is_(None),
            or_(Row.locked_at.is_(None), Row.locked_at < stale),
        )
        .values(locked_at=func.clock_timestamp())
        .returning(Row.key)
    ).scalar_one_or_none()
    return taken is not None


def store(db: Session, asked: Asked, status: int, body: BaseModel) -> None:
    """Keep the answer under the key, and let go of the lock."""
    db.execute(
        update(Row)
        .where(*_mine(asked))
        .values(response_status=status, response_body=body.model_dump(mode="json"), locked_at=None)
    )


def release(db: Session, asked: Asked) -> None:
    """Let go without an answer: the request failed, and the same key may try again."""
    db.execute(
        update(Row).where(*_mine(asked), Row.response_status.is_(None)).values(locked_at=None)
    )


def replay(response: Response, stored: Stored) -> dict[str, Any]:
    """Set the stored status and the replay header; the caller returns the body."""
    response.status_code = stored.status
    response.headers[REPLAYED_HEADER] = "true"
    return stored.body


def delete_expired(db: Session) -> int:
    """Remove keys past their 24 hours. Nobody reads them; they only take up room."""
    return db.execute(delete(Row).where(Row.expires_at <= func.now())).rowcount
