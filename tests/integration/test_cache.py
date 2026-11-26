"""The cache: values, versions, a dead Redis, and versions bumped by the session after commit."""

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import Connection, func, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.redis import Breaker, Cache, create_redis
from app.infrastructure.redis.invalidation import attach
from app.modules.events.models import Event
from app.modules.listings.models import Listing, ListingStatus
from app.modules.listings.state import transition as move_listing
from app.workers import jobs, runtime
from tests.integration.factories import (
    make_event,
    make_listing,
    make_reservation,
    make_ticket,
    make_user,
)
from tests.integration.redis_support import nowhere, redis_url  # noqa: F401

pytestmark = pytest.mark.integration


def cache_at(url: str, breaker: Breaker | None = None) -> Cache:
    settings = Settings(_env_file=None, redis_url=url)
    return Cache(create_redis(settings), breaker or Breaker(), ttl=(30, 60))


@pytest.fixture
def cache(redis_url: str) -> Cache:  # noqa: F811
    return cache_at(redis_url)


@pytest.fixture
def sessions(db_connection: Connection, cache: Cache) -> Iterator[sessionmaker[Session]]:
    """Sessions that bump `cache` after they commit, as the API's do."""
    factory = create_session_factory(db_connection)
    factory.configure(join_transaction_mode="create_savepoint", info={"cache": cache})
    yield factory


def versions(cache: Cache) -> tuple[int, int]:
    found = cache.versions("listings", "events")
    assert found is not None
    listings, events = found
    return listings, events


# ---------------------------------------------------------------- values and versions


def test_a_value_comes_back_and_expires_in_30_to_60_seconds(cache: Cache, redis_url: str) -> None:  # noqa: F811
    cache.set("cache:test", b'{"a": 1}')

    assert cache.get("cache:test") == b'{"a": 1}'
    ttl = Redis.from_url(redis_url).ttl("cache:test")
    assert 30 <= ttl <= 60


def test_ttls_are_spread_out(cache: Cache, redis_url: str) -> None:  # noqa: F811
    for n in range(20):
        cache.set(f"cache:spread:{n}", b"x")
    client = Redis.from_url(redis_url)
    assert len({client.ttl(f"cache:spread:{n}") for n in range(20)}) > 1


def test_versions_start_at_zero_and_go_up_by_one(cache: Cache) -> None:
    assert versions(cache) == (0, 0)
    cache.bump("listings")
    cache.bump("listings", "events")
    assert versions(cache) == (2, 1)


def test_a_dead_redis_answers_nothing_and_is_left_alone_for_a_while() -> None:
    breaker = Breaker(pause=5.0)
    dead = cache_at(nowhere(), breaker)

    assert dead.get("cache:x") is None
    assert not breaker.closed()
    started = time.monotonic()
    assert dead.versions("listings") is None
    dead.set("cache:x", b"x")
    dead.bump("listings")
    assert time.monotonic() - started < 0.05  # the breaker answered, not a timeout


def test_a_cache_with_no_client_is_off() -> None:
    off = Cache(None, Breaker(), ttl=(30, 60))
    assert not off.enabled
    assert off.versions("listings") is None
    assert off.get("cache:x") is None


# ---------------------------------------------------------------- invalidation by the session


def test_a_listing_status_change_bumps_listings_after_the_commit(
    sessions: sessionmaker[Session], cache: Cache
) -> None:
    with sessions() as db:
        listing = make_listing(db, status="ACTIVE")
        db.commit()
    before = versions(cache)

    with sessions() as db:
        listing = db.get_one(Listing, listing.id)
        move_listing(db, listing, ListingStatus.CANCELLED, actor_id=None)
        assert versions(cache) == before  # not yet: readers would refill with the old rows
        db.commit()

    assert versions(cache) == (before[0] + 1, before[1])


def test_the_bump_happens_once_the_transaction_is_over(
    db_connection: Connection,
    redis_url: str,  # noqa: F811
) -> None:
    # Bumped inside the transaction, a reader could refill the new version with the rows from
    # before the commit, and keep serving them. The test's sessions write inside a savepoint of
    # `db_connection`; once the session has committed, that savepoint is released.
    during: list[bool] = []

    class Watching(Cache):
        def bump(self, *names: str) -> None:
            during.append(db_connection.in_nested_transaction())
            super().bump(*names)

    watching = Watching(
        create_redis(Settings(_env_file=None, redis_url=redis_url)), Breaker(), ttl=(30, 60)
    )
    factory = create_session_factory(db_connection)
    factory.configure(join_transaction_mode="create_savepoint", info={"cache": watching})

    with factory() as db:
        make_listing(db, status="ACTIVE")
        db.commit()

    assert during == [False]


class Watching(Cache):
    """A cache that notes, at each bump, whether the test session's savepoint is still open:
    the test's sessions write inside one on `connection`, released when the session commits."""

    def __init__(self, url: str, connection: Connection) -> None:
        super().__init__(
            create_redis(Settings(_env_file=None, redis_url=url)), Breaker(), ttl=(30, 60)
        )
        self.connection = connection
        self.during: list[bool] = []

    def bump(self, *names: str) -> None:
        self.during.append(self.connection.in_nested_transaction())
        super().bump(*names)


def test_a_savepoint_released_inside_the_transaction_bumps_nothing_yet(
    db_connection: Connection,
    redis_url: str,  # noqa: F811
) -> None:
    # Reserving, a failed payment and the expiry job each release a savepoint before they
    # commit. SQLAlchemy calls after_commit for that release too.
    watching = Watching(redis_url, db_connection)
    factory = create_session_factory(db_connection)
    factory.configure(join_transaction_mode="create_savepoint", info={"cache": watching})

    with factory() as db:
        listing = make_listing(db, status="ACTIVE")
        db.commit()
    watching.during.clear()

    with factory() as db:
        db.execute(
            update(Listing)
            .where(Listing.id == listing.id)
            .values(updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        with db.begin_nested():
            make_user(db)
        with db.begin_nested():
            make_listing(db, status="ACTIVE")
        assert watching.during == []
        db.commit()

    assert watching.during == [False]  # once, after the transaction's own commit


def test_the_workers_sessions_carry_a_cache_that_checks_redis_with_a_ping(
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
    redis_url: str,  # noqa: F811
) -> None:
    settings = test_settings.model_copy(update={"redis_url": redis_url, "cache_enabled": True})
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    runtime.sessions.cache_clear()
    runtime.search_cache.cache_clear()
    try:
        found = runtime.sessions().kw["info"]["cache"]
        assert found is runtime.search_cache()
        assert isinstance(found, Cache) and found.enabled
        assert found._breaker.probe is not None  # noqa: SLF001
    finally:
        runtime.sessions.cache_clear()
        runtime.search_cache.cache_clear()


def test_the_expiry_job_bumps_once_its_batch_has_committed(
    db_connection: Connection,
    redis_url: str,  # noqa: F811
) -> None:
    watching = Watching(redis_url, db_connection)
    factory = create_session_factory(db_connection)
    factory.configure(join_transaction_mode="create_savepoint", info={"cache": watching})
    with factory() as db:
        for _ in range(3):
            make_reservation(db, expires_at=datetime.now(UTC) - timedelta(minutes=1))
        db.commit()
    watching.during.clear()
    before = versions(watching)

    assert jobs.expire_due_reservations(factory) == 3

    assert watching.during == [False]  # one batch, one bump, after its commit
    assert versions(watching)[0] == before[0] + 1


def test_a_bump_missed_while_redis_was_away_is_made_once_it_is_back(
    db_connection: Connection,
    cache: Cache,
    redis_url: str,  # noqa: F811
) -> None:
    # Redis away for this process only (a timeout, a blip): other processes still serve the
    # old version's pages, so the bump is owed, and made by the first call once it is back.
    now = [100.0]
    breaker = Breaker(pause=5.0, clock=lambda: now[0])
    away = Cache(create_redis(Settings(_env_file=None, redis_url=redis_url)), breaker, ttl=(30, 60))
    factory = create_session_factory(db_connection)
    factory.configure(join_transaction_mode="create_savepoint", info={"cache": away})
    before = versions(cache)

    breaker.trip(RedisConnectionError("timed out"))
    with factory() as db:
        make_listing(db, status="ACTIVE")
        db.commit()
    assert versions(cache) == before

    now[0] = 105.0
    away.get("cache:anything")
    assert versions(cache)[0] == before[0] + 1
    away.get("cache:anything")
    assert versions(cache)[0] == before[0] + 1  # owed once, paid once


def test_a_bulk_listing_update_bumps_listings(
    sessions: sessionmaker[Session], cache: Cache
) -> None:
    with sessions() as db:
        listing = make_listing(db, status="ACTIVE")
        db.commit()
    before = versions(cache)

    with sessions() as db:
        db.execute(
            update(Listing)
            .where(Listing.id == listing.id)
            .values(updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        db.commit()

    assert versions(cache)[0] == before[0] + 1


def test_a_new_listing_bumps_listings(sessions: sessionmaker[Session], cache: Cache) -> None:
    with sessions() as db:
        ticket = make_ticket(db)
        db.commit()
    before = versions(cache)

    with sessions() as db:
        make_listing(db, ticket=db.get_one(type(ticket), ticket.id))
        db.commit()

    assert versions(cache)[0] == before[0] + 1


def test_an_event_change_bumps_events_and_listings(
    sessions: sessionmaker[Session], cache: Cache
) -> None:
    with sessions() as db:
        event = make_event(db, status="PUBLISHED")
        db.commit()
    before = versions(cache)

    with sessions() as db:
        db.execute(update(Event).where(Event.id == event.id).values(name="Renamed"))
        db.commit()

    assert versions(cache) == (before[0] + 1, before[1] + 1)


def test_a_rolled_back_change_bumps_nothing(sessions: sessionmaker[Session], cache: Cache) -> None:
    before = versions(cache)

    with sessions() as db:
        make_listing(db, status="ACTIVE")
        db.rollback()
        db.commit()  # an empty commit after the rollback must not bump either

    assert versions(cache) == before


def test_other_tables_bump_nothing(sessions: sessionmaker[Session], cache: Cache) -> None:
    before = versions(cache)

    with sessions() as db:
        make_user(db)
        db.commit()

    assert versions(cache) == before


def test_a_session_without_a_cache_bumps_nothing_and_still_commits(
    db_connection: Connection, cache: Cache
) -> None:
    before = versions(cache)
    plain = create_session_factory(db_connection)
    plain.configure(join_transaction_mode="create_savepoint")

    with plain() as db:
        make_listing(db, status="ACTIVE")
        db.commit()

    assert versions(cache) == before


def test_attach_gives_a_session_its_cache(db_connection: Connection, cache: Cache) -> None:
    plain = create_session_factory(db_connection)
    plain.configure(join_transaction_mode="create_savepoint")
    before = versions(cache)

    with plain() as db:
        attach(db, cache)
        make_listing(db, status="ACTIVE")
        db.commit()

    assert versions(cache)[0] == before[0] + 1


def test_a_dead_redis_at_commit_time_does_not_fail_the_commit(
    db_connection: Connection,
) -> None:
    dead = cache_at(nowhere())
    factory = create_session_factory(db_connection)
    factory.configure(join_transaction_mode="create_savepoint", info={"cache": dead})

    with factory() as db:
        listing = make_listing(db, status="ACTIVE")
        db.commit()

    with factory() as db:
        assert db.get(Listing, listing.id) is not None
