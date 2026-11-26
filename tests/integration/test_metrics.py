"""Metrics: per-route HTTP numbers on /metrics, the purchase counters, and the gauges read when
Prometheus scrapes. No label ever carries an id."""

import json
import re
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.infrastructure.redis import stats
from app.main import create_app
from app.modules.outbox.relay import PublishError, relay_once
from app.modules.outbox.service import EventType, emit
from tests.integration.api import bearer, problem_code
from tests.integration.conftest import new_account
from tests.integration.factories import make_listing
from tests.integration.payments import (
    event,
    fakepay,  # noqa: F401
    paying,
    post_webhook,
)

pytestmark = pytest.mark.integration

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def scrape(api: TestClient) -> str:
    response = api.get("/metrics")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/plain")
    return response.text


def test_requests_are_counted_by_route_never_by_id(api: TestClient, db_session: Session) -> None:
    listing = make_listing(db_session, status="ACTIVE")
    api.get(f"/api/v1/listings/{listing.id}")
    api.get(f"/api/v1/listings/{uuid.uuid4()}")
    api.get(f"/api/v1/no/such/route/{uuid.uuid4()}")

    text = scrape(api)

    assert 'handler="/api/v1/listings/{listing_id}"' in text
    assert "http_request_duration_seconds_bucket" in text
    assert UUID.search(text) is None


def test_each_reservation_attempt_is_counted_by_its_result(
    api: TestClient, db_session: Session
) -> None:
    listing = make_listing(db_session, status="ACTIVE")
    buyer, rival = new_account(api), new_account(api)
    before = {
        result: value("reservation_attempts_total", result=result)
        for result in ("reserved", "already_yours", "listing-not-available", "listing-not-found")
    }

    path = f"/api/v1/listings/{listing.id}/reservations"
    assert api.post(path, headers=bearer(buyer["token"])).status_code == 201
    assert api.post(path, headers=bearer(buyer["token"])).status_code == 200
    refused = api.post(path, headers=bearer(rival["token"]))
    assert problem_code(refused) == "listing-not-available"
    missing = api.post(
        f"/api/v1/listings/{uuid.uuid4()}/reservations", headers=bearer(rival["token"])
    )
    assert missing.status_code == 404

    for result, was in before.items():
        assert value("reservation_attempts_total", result=result) == was + 1, result


def test_webhooks_are_counted_by_type_and_outcome_and_timed(
    api: TestClient,
    db_session: Session,
    fakepay: Any,  # noqa: F811
) -> None:
    _, _, intent = paying(api, db_session)
    succeeded = event(intent)
    labels: list[dict[str, str]] = [
        {"type": "payment.succeeded", "outcome": "processed"},
        {"type": "payment.succeeded", "outcome": "duplicate"},
        {"type": "unknown", "outcome": "rejected"},
    ]
    before = [value("payment_events_total", **one) for one in labels]
    timed = value("payment_webhook_seconds_count")

    assert post_webhook(api, succeeded).status_code == 200
    assert post_webhook(api, succeeded).status_code == 200
    assert post_webhook(api, succeeded, secret="not-the-secret-" * 3).status_code == 401

    for one, was in zip(labels, before, strict=True):
        assert value("payment_events_total", **one) == was + 1, one
    assert value("payment_webhook_seconds_count") == timed + 3


def test_the_outbox_backlog_is_counted_when_scraped(api: TestClient, db_session: Session) -> None:
    before = value("outbox_backlog")
    for _ in range(2):
        emit(
            db_session,
            aggregate_type="test",
            aggregate_id=uuid.uuid4(),
            event_type=EventType.PAYMENT_SUCCEEDED,
            payload={},
        )

    assert value("outbox_backlog") == before + 2
    assert value("outbox_oldest_unpublished_seconds") >= 0
    assert "outbox_backlog" in scrape(api)


def test_the_relay_counts_what_it_published_and_what_failed(
    api: TestClient, db_session: Session
) -> None:
    class Accepting:
        def publish(self, consumer: Any, message: dict[str, Any]) -> None:
            pass

    class Refusing:
        def publish(self, consumer: Any, message: dict[str, Any]) -> None:
            raise PublishError("broker down")

    sessions = api.app.state.session_factory  # type: ignore[attr-defined]
    failed, published = value("outbox_publish_failures_total"), value("outbox_published_total")

    def one_event() -> None:
        emit(
            db_session,
            aggregate_type="test",
            aggregate_id=uuid.uuid4(),
            event_type=EventType.PAYMENT_SUCCEEDED,
            payload={},
        )
        db_session.commit()

    one_event()
    relay_once(sessions, Accepting())
    assert value("outbox_published_total") == published + 1

    one_event()
    relay_once(sessions, Refusing())
    assert value("outbox_publish_failures_total") == failed + 1
    assert value("outbox_published_total") == published + 1


DASHBOARD = Path(__file__).parents[2] / "observability/grafana/dashboards/ticketloop.json"


def test_every_metric_the_dashboard_reads_is_exported(api: TestClient) -> None:
    api.get("/api/v1/health")  # so the per-route histogram has a series
    text = scrape(api)
    panels = json.loads(DASHBOARD.read_text("utf-8"))["panels"]
    expressions = [t["expr"] for panel in panels for t in panel.get("targets", [])]
    names = {
        name
        for expr in expressions
        for name in re.findall(r"\b[a-z]+(?:_[a-z0-9]+)+\b", expr)
        if name != "histogram_quantile" and not name.startswith("rabbitmq_")
    }

    assert len(names) >= 14
    assert sorted(name for name in names if name not in text) == []


def test_the_pool_and_the_redis_counters_are_exported(api: TestClient) -> None:
    hits = value("cache_requests_total", outcome="hit")
    refused = value("rate_limit_decisions_total", decision="refused")
    stats.add("cache_hits")
    stats.add("rate_limit_refused")

    text = scrape(api)

    assert "db_pool_checked_out_connections" in text
    assert value("cache_requests_total", outcome="hit") == hits + 1
    assert value("rate_limit_decisions_total", decision="refused") == refused + 1


@pytest.fixture
def without_postgres(test_settings: Settings) -> Iterator[TestClient]:
    dead = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none"
    app = create_app(
        test_settings.model_copy(update={"database_url": dead, "db_connect_timeout_seconds": 2})
    )
    with TestClient(app) as client:
        yield client


@pytest.fixture
def busy_pool(test_settings: Settings) -> Iterator[TestClient]:
    """The real app (its sessions on its own pool, not a test transaction), with every
    connection of that pool held, as by a load test's requests."""
    app = create_app(test_settings)
    with TestClient(app) as client:
        pool: Any = app.state.engine.pool
        held = [app.state.engine.connect() for _ in range(pool.size() + pool._max_overflow)]
        try:
            yield client
        finally:
            for connection in held:
                connection.close()


def test_metrics_answer_at_once_with_the_backlog_while_requests_hold_the_pool(
    busy_pool: TestClient,
) -> None:
    started = time.monotonic()
    text = scrape(busy_pool)

    # Not after the pool's 30-second wait for a free connection: Prometheus would have given
    # up on the whole scrape at 5 s, and every API series would have a gap.
    assert time.monotonic() - started < 2
    assert "\noutbox_backlog " in text


def test_metrics_answer_quickly_without_the_backlog_when_postgres_is_down(
    without_postgres: TestClient,
) -> None:
    started = time.monotonic()
    text = scrape(without_postgres)

    # Within the connect timeout (2 s; a refused connection on Windows takes about that long),
    # and inside Prometheus's scrape timeout, which is the 5-second scrape interval.
    assert time.monotonic() - started < 4.5
    assert "http_requests_total" in text or "python_info" in text
    assert "\noutbox_backlog " not in text
