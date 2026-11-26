import threading
import time
from collections.abc import Callable

import structlog

log = structlog.get_logger(__name__)


def in_background(check: Callable[[], None]) -> None:
    threading.Thread(target=check, name="redis-check", daemon=True).start()


class Breaker:
    """After Redis fails, stop asking it until it answers again.

    Without this every request would wait out a timeout while Redis is down. With it, the
    request that finds Redis gone pays once, and the rest go straight to Postgres. Every
    `pause` seconds `probe` (a PING) asks whether Redis is back, from a thread of its own: a
    request never asks a Redis that was down, because that can cost more than any timeout (in
    Docker, a stopped container's name takes seconds to fail to resolve). Shared by the cache
    and the limiter, since they share the Redis.

    With no `probe`, the first request after the pause asks instead.
    """

    def __init__(
        self,
        pause: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        probe: Callable[[], object] | None = None,
        start: Callable[[Callable[[], None]], None] = in_background,
    ) -> None:
        self.pause = pause
        self.probe = probe
        self._clock = clock
        self._start = start
        self._lock = threading.Lock()
        self._down = False
        self._retry_at = 0.0
        self._checking = False

    def closed(self) -> bool:
        """True when Redis may be asked."""
        if not self._down:
            return True
        if self.probe is None:
            return self._clock() >= self._retry_at
        with self._lock:
            due = not self._checking and self._clock() >= self._retry_at
            self._checking = self._checking or due
        if due:
            self._start(self._check)
        return not self._down

    def trip(self, error: Exception) -> None:
        with self._lock:
            if not self._down:  # once per outage, not once per request
                log.warning("redis.unavailable", error=str(error), pause_seconds=self.pause)
            self._down = True
            self._retry_at = self._clock() + self.pause

    def _check(self) -> None:
        assert self.probe is not None
        try:
            self.probe()
        except Exception:  # noqa: BLE001 - any failure means "not yet"
            with self._lock:
                self._retry_at = self._clock() + self.pause
                self._checking = False
            return
        with self._lock:
            self._down = False
            self._checking = False
        log.info("redis.available")
