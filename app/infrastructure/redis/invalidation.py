"""Cache invalidation found by the session, not called by the services.

Any insert, update or delete on `listings` or `events` marks the session, whether it is a
state machine's conditional UPDATE, a bulk UPDATE or an object added and flushed. When that
session commits, the versions those tables feed are bumped; when it rolls back, the mark is
dropped. A new write path cannot forget to invalidate, because it writes through a session.

The bump runs after the commit, never before: bumped earlier, a reader could fill the new
version with rows from before the commit. Releasing a savepoint is not that commit. And it
never raises: the rows are already committed. A bump Redis can't take right now is owed, and
the cache makes it on its next call once Redis answers (see `Cache.bump`).

A session bumps the cache it finds in `session.info["cache"]`: `get_db` attaches the app's, and
the worker's session factory carries one in its `info`.
"""

from itertools import chain
from typing import Any

import structlog
from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, SessionTransaction

from app.infrastructure.redis.cache import Cache

log = structlog.get_logger(__name__)

# Which versions a write to each table makes stale. An event's status decides which listings
# search shows, so an event write reaches search too.
FEEDS: dict[str, tuple[str, ...]] = {
    "listings": ("listings",),
    "events": ("events", "listings"),
}

CACHE = "cache"
_STALE = "cache_stale"


def attach(session: Session, cache: Cache | None) -> None:
    session.info[CACHE] = cache


def _mark(session: Session, table: str | None) -> None:
    names = FEEDS.get(table or "")
    if names:
        session.info.setdefault(_STALE, set()).update(names)


@event.listens_for(Session, "do_orm_execute")
def _on_statement(state: ORMExecuteState) -> None:
    if state.is_insert or state.is_update or state.is_delete:
        for mapper in state.all_mappers:
            _mark(state.session, getattr(mapper.local_table, "name", None))


@event.listens_for(Session, "after_flush")
def _on_flush(session: Session, _context: Any) -> None:
    for row in chain(session.new, session.dirty, session.deleted):
        _mark(session, getattr(getattr(row, "__table__", None), "name", None))


@event.listens_for(Session, "after_commit")
def _on_commit(session: Session) -> None:
    # SQLAlchemy calls this when a savepoint is released too (reserving, a failed payment and
    # the expiry job each use one), while the transaction is still open. Bumped then, a reader
    # could fill the new version with rows from before the commit. The marks wait for it.
    if session.in_nested_transaction():
        return
    stale = session.info.pop(_STALE, None)
    cache = session.info.get(CACHE)
    if not stale or cache is None:
        return
    try:
        cache.bump(*sorted(stale))
    except Exception:  # noqa: BLE001 - the commit has happened; never turn it into an error
        log.exception("cache.bump_failed", names=sorted(stale))


@event.listens_for(Session, "after_soft_rollback")
def _on_rollback(session: Session, previous: SessionTransaction) -> None:
    # The whole transaction, not a savepoint inside it: a rolled-back savepoint leaves the
    # marks of the rest, and at worst one bump too many.
    if previous.parent is None:
        session.info.pop(_STALE, None)
