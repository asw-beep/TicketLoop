import threading
from collections import Counter


class Stats:
    """Counts kept in the process: cache hits, misses and errors, and what the limiter did.
    Enough to measure a hit ratio now; M11 exports them to Prometheus."""

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._lock = threading.Lock()  # routes run in a thread pool

    def add(self, name: str) -> None:
        with self._lock:
            self._counts[name] += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)


stats = Stats()
