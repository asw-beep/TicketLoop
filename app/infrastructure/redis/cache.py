"""Cache-aside with versioned keys.

Every key a reader builds contains the current version of what it depends on, e.g.
`cache:listings:l41:<hash of the query>`. A write never deletes keys: it bumps the version after
its commit, and the old keys become unreachable and expire on their own. The reader reads the
version before it queries Postgres, so a page computed from rows a writer was about to change
is stored under the old version, where nobody will look again.
"""

import random
import threading
from collections.abc import Callable, Iterable

from redis import Redis
from redis.exceptions import RedisError

from app.infrastructure.redis.breaker import Breaker
from app.infrastructure.redis.stats import stats

VERSION_KEY = "cache:version:{}"


class Cache:
    def __init__(
        self,
        client: Redis | None,
        breaker: Breaker,
        *,
        ttl: tuple[int, int],
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._client = client
        self._breaker = breaker
        self._ttl = ttl
        self._rand = rand
        self._lock = threading.Lock()
        self._owed: set[str] = set()

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def versions(self, *names: str) -> tuple[int, ...] | None:
        """The current versions, 0 for one never bumped. None: no cache right now."""
        found = self._ask(lambda client: client.mget([VERSION_KEY.format(n) for n in names]))
        if found is None:
            return None
        return tuple(int(value or 0) for value in found)

    def get(self, key: str) -> bytes | None:
        found = self._ask(lambda client: client.get(key))
        return found if isinstance(found, bytes) else None

    def set(self, key: str, value: bytes) -> None:
        """Stored for 30 to 60 seconds, a different time for each key, so a burst of entries
        filled together does not expire together and send every reader to Postgres at once."""
        low, high = self._ttl
        seconds = low + int(self._rand() * (high - low + 1))
        self._ask(lambda client: client.set(key, value, ex=min(seconds, high)))

    def bump(self, *names: str) -> None:
        """Make every entry that depends on these unreachable.

        If Redis can't be asked right now (this process's breaker is open, or the call fails),
        the bump is owed, and made by this cache's first call once Redis answers: other
        processes may be serving the old version, and should stop as soon as possible."""
        if self._client is None:
            return
        if self._ask(lambda client: _incr(client, names)) is None:
            stats.add("cache_bumps_owed")
            self._owe(names)

    def _ask[T](self, command: Callable[[Redis], T]) -> T | None:
        if self._client is None or not self._breaker.closed():
            return None
        with self._lock:
            owed, self._owed = self._owed, set()
        try:
            if owed:
                _incr(self._client, owed)
            return command(self._client)
        except RedisError as error:
            self._owe(owed)  # paid already if only `command` failed: one bump too many is harmless
            stats.add("cache_errors")
            self._breaker.trip(error)
            return None

    def _owe(self, names: Iterable[str]) -> None:
        with self._lock:
            self._owed.update(names)


def _incr(client: Redis, names: Iterable[str]) -> bool:
    with client.pipeline(transaction=False) as pipe:
        for name in sorted(names):
            pipe.incr(VERSION_KEY.format(name))
        pipe.execute()
    return True
