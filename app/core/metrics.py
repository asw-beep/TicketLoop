"""Prometheus metrics: what the purchase flow did, and what a process holds right now.

Counters live in prometheus_client's default registry, one set per process. Every label takes a
value from a short fixed list, never a caller's input or an id, so the number of series stays
small whatever happens: a webhook with a made-up event type counts as `unknown`.

Three things are read only when Prometheus scrapes: the outbox backlog (one indexed query, on a
connection of its own so it never waits behind requests for the pool, with a 1-second limit,
skipped if Postgres is down), connections checked out of the pool, and the cache and
rate-limit counts M10 keeps in `app.infrastructure.redis.stats`.
"""

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import structlog
from fastapi import FastAPI
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.multiprocess import MultiProcessCollector
from prometheus_client.registry import Collector
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_fastapi_instrumentator import metrics as http_metrics
from sqlalchemy import func, select, text

from app.infrastructure.redis import stats
from app.modules.outbox.models import OutboxEvent

log = structlog.get_logger(__name__)

RESERVATION_RESULTS = frozenset(
    {
        "reserved",
        "already_yours",
        "listing-not-available",
        "listing-not-found",
        "own-listing-reservation",
        "buyer-only",
    }
)
PAYMENT_TYPES = frozenset({"payment.succeeded", "payment.failed"})

RESERVATION_ATTEMPTS = Counter(
    "reservation_attempts", "Reservation attempts, by result.", ["result"]
)
PAYMENT_EVENTS = Counter(
    "payment_events",
    "Payment gateway webhooks, by event type and what was done with them.",
    ["type", "outcome"],
)
WEBHOOK_SECONDS = Histogram(
    "payment_webhook_seconds",
    "Time to verify and apply one payment webhook.",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)
WORKER_TASKS = Counter(
    "worker_tasks", "Consumer task deliveries, by how they ended.", ["task", "outcome"]
)
OUTBOX_PUBLISHED = Counter("outbox_published", "Outbox events the relay published.")
OUTBOX_FAILURES = Counter(
    "outbox_publish_failures", "Relay passes stopped by a publish the broker refused."
)

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)

# Made once per process and shared by every app: made again (a test builds many apps), the
# instrumentator finds the name taken and hands back nothing, and that app counts nothing.
_HTTP = [
    http_metrics.requests(),
    http_metrics.latency(buckets=LATENCY_BUCKETS),
]

_CACHE = {
    "cache_hits": "hit",
    "cache_misses": "miss",
    "cache_bypassed": "bypass",
    "cache_errors": "error",
}
_LIMITS = {
    "rate_limit_allowed": "allowed",
    "rate_limit_refused": "refused",
    "rate_limit_skipped": "skipped",
    "rate_limit_errors": "error",
}


def reservation_result(code: str) -> str:
    return code if code in RESERVATION_RESULTS else "other"


def payment_type(event_type: str | None) -> str:
    return event_type if event_type in PAYMENT_TYPES else "unknown"


class _AtScrape(Collector):
    """The values read when Prometheus asks, from the app `watch()` last pointed it at."""

    def __init__(self) -> None:
        self.app: FastAPI | None = None

    def describe(self) -> list[Metric]:
        return []  # nothing at registration: collect() may query Postgres

    def collect(self) -> Iterator[Metric]:
        yield from _redis_counts()
        app = self.app
        if app is None:
            return
        pool = getattr(app.state.engine, "pool", None)
        if pool is not None and hasattr(pool, "checkedout"):
            checked_out = GaugeMetricFamily(
                "db_pool_checked_out_connections",
                "Database connections checked out of this process's pool right now.",
            )
            checked_out.add_metric([], pool.checkedout())
            yield checked_out
        yield from _backlog(app)


def _redis_counts() -> Iterator[Metric]:
    counts = stats.snapshot()
    cache = CounterMetricFamily(
        "cache_requests", "Cached reads, by how they were answered.", labels=["outcome"]
    )
    for key, outcome in _CACHE.items():
        cache.add_metric([outcome], counts.get(key, 0))
    yield cache
    owed = CounterMetricFamily(
        "cache_bumps_owed", "Version bumps Redis could not take at once, made later."
    )
    owed.add_metric([], counts.get("cache_bumps_owed", 0))
    yield owed
    limits = CounterMetricFamily(
        "rate_limit_decisions", "Rate-limit checks, by decision.", labels=["decision"]
    )
    for key, decision in _LIMITS.items():
        limits.add_metric([decision], counts.get(key, 0))
    yield limits


def _backlog(app: FastAPI) -> Iterator[Metric]:
    sessions = getattr(app.state, "metrics_sessions", None)
    if sessions is None:
        return
    try:
        with sessions() as db:
            db.execute(text("SET LOCAL statement_timeout = 1000"))
            count, oldest = db.execute(
                select(
                    func.count(),
                    func.greatest(
                        func.extract(
                            "epoch", func.clock_timestamp() - func.min(OutboxEvent.created_at)
                        ),
                        0,
                    ),
                ).where(OutboxEvent.published_at.is_(None))
            ).one()
    except Exception as error:  # noqa: BLE001 - a scrape must answer without Postgres
        log.warning("metrics.backlog_unavailable", error=str(error))
        return
    backlog = GaugeMetricFamily("outbox_backlog", "Outbox events not yet published.")
    backlog.add_metric([], count)
    yield backlog
    age = GaugeMetricFamily(
        "outbox_oldest_unpublished_seconds", "Age of the oldest unpublished outbox event."
    )
    age.add_metric([], float(oldest))
    yield age


_AT_SCRAPE = _AtScrape()
REGISTRY.register(_AT_SCRAPE)


def watch(app: FastAPI) -> None:
    """Read the scrape-time values from this app's engine and sessions. One app per process;
    a test that builds another moves the reading to it."""
    _AT_SCRAPE.app = app


def expose(app: FastAPI) -> None:
    """Per-route request counts and latencies, and `/metrics` to read everything from. Routes
    are labelled by their template, and a path that matches no route by `none`."""
    instrumentator = Instrumentator(
        should_group_status_codes=True,
        should_group_untemplated=True,
        excluded_handlers=["/metrics"],
    )
    for instrumentation in _HTTP:
        if instrumentation is not None:
            instrumentator.add(instrumentation)
    instrumentator.instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    watch(app)


def serve_worker_metrics(port: int) -> None:
    """For the Celery worker's main process: its prefork children write their counters to
    PROMETHEUS_MULTIPROC_DIR, and this serves their sum. The directory is emptied first, so a
    restarted worker does not add up the counters of processes that are gone."""
    directory = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if not port or not directory:
        return
    shutil.rmtree(directory, ignore_errors=True)
    Path(directory).mkdir(parents=True, exist_ok=True)
    registry = CollectorRegistry()
    MultiProcessCollector(registry)
    start_http_server(port, registry=registry)
    log.info("metrics.serving", port=port)
