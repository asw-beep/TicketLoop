"""The Redis pieces that need no server: settings, the client's timeouts, the breaker, keys."""

import time
from collections.abc import Callable
from typing import cast

import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import Settings
from app.infrastructure.redis import Breaker, Cache, create_redis
from app.infrastructure.redis.limiter import login_identity, retry_after_seconds
from app.main import create_redis_parts


def test_the_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.cache_enabled is True
    assert settings.rate_limit_enabled is True
    assert (settings.cache_ttl_min_seconds, settings.cache_ttl_max_seconds) == (30, 60)
    assert (
        settings.rate_limit_login_per_minute,
        settings.rate_limit_reserve_per_minute,
        settings.rate_limit_payment_per_minute,
        settings.rate_limit_search_per_minute,
    ) == (5, 10, 10, 60)


def test_the_client_gives_up_quickly() -> None:
    client = create_redis(Settings(_env_file=None, redis_timeout_seconds=0.25))
    options = client.connection_pool.connection_kwargs
    assert options["socket_timeout"] == 0.25
    assert options["socket_connect_timeout"] == 0.25
    # No retries: a dead Redis costs one timeout, then the breaker takes over.
    assert options["retry"].get_retries() == 0


def test_the_breaker_stops_asking_for_a_while() -> None:
    now = [100.0]
    breaker = Breaker(pause=5.0, clock=lambda: now[0])
    assert breaker.closed()
    breaker.trip(ConnectionError("refused"))
    assert not breaker.closed()
    now[0] = 104.9
    assert not breaker.closed()
    now[0] = 105.0
    assert breaker.closed()


def test_with_a_probe_a_request_never_asks_a_redis_that_was_down() -> None:
    # In Docker a stopped Redis's name takes seconds to fail to resolve, a wait no socket
    # timeout covers. A check in the background pays it, not a request.
    now = [100.0]
    started: list[Callable[[], None]] = []
    pings: list[str] = []
    breaker = Breaker(
        pause=5.0, clock=lambda: now[0], probe=lambda: pings.append("ping"), start=started.append
    )
    breaker.trip(ConnectionError("refused"))
    now[0] = 105.0
    assert not breaker.closed()  # the pause is over, and still no request asks Redis
    assert not breaker.closed()
    assert len(started) == 1  # one check, however many requests
    started.pop()()  # the check runs, and Redis answers
    assert pings == ["ping"]
    assert breaker.closed()


def test_a_failed_check_waits_another_pause() -> None:
    now = [100.0]
    pings: list[float] = []

    def still_down() -> None:
        pings.append(now[0])
        raise RedisConnectionError("refused")

    breaker = Breaker(pause=5.0, clock=lambda: now[0], probe=still_down, start=lambda c: c())
    breaker.trip(RedisConnectionError("refused"))
    now[0] = 105.0
    assert not breaker.closed()
    now[0] = 109.9
    assert not breaker.closed()
    now[0] = 110.0
    assert not breaker.closed()
    assert pings == [105.0, 110.0]


def test_a_redis_that_hangs_costs_one_request_not_one_every_pause() -> None:
    class Hanging:
        """A Redis whose name takes 0.3 seconds to fail to resolve."""

        def get(self, key: str) -> None:
            time.sleep(0.3)
            raise RedisConnectionError("Name or service not known")

        def ping(self) -> None:
            self.get("ping")

    hanging = Hanging()
    cache = Cache(cast(Redis, hanging), Breaker(pause=0.05, probe=hanging.ping), ttl=(30, 60))
    cache.get("cache:x")  # the request that finds Redis gone pays, once
    time.sleep(0.1)  # past the pause
    slowest = 0.0
    for _ in range(5):
        started = time.monotonic()
        assert cache.get("cache:x") is None
        slowest = max(slowest, time.monotonic() - started)
        time.sleep(0.06)
    assert slowest < 0.1


def test_the_apps_breaker_checks_redis_with_a_ping() -> None:
    client, cache, _ = create_redis_parts(Settings(_env_file=None))
    assert client is not None
    assert cache._breaker.probe == client.ping  # noqa: SLF001


def test_a_login_bucket_ignores_case_and_spaces_and_hides_the_email() -> None:
    same = login_identity(" Asha@Example.com ", "10.0.0.1")
    assert same == login_identity("asha@example.com", "10.0.0.1")
    assert same != login_identity("asha@example.com", "10.0.0.2")
    assert same != login_identity("ravi@example.com", "10.0.0.1")
    assert "asha" not in same


@pytest.mark.parametrize(
    ("wait", "seconds"), [(0.001, 1), (1.0, 1), (1.2, 2), (11.99, 12), (59.5, 60)]
)
def test_retry_after_is_whole_seconds_and_never_zero(wait: float, seconds: int) -> None:
    assert retry_after_seconds(wait) == seconds
