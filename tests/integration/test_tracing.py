"""Traces: a request's trace is stored with the events it emits, continued by the relay, carried
to the worker in the Celery message, and its trace id is on every log line written inside it."""

import uuid
from collections.abc import Iterator
from typing import Any

import httpx2 as httpx
import pytest
from celery.signals import before_task_publish
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core import telemetry
from app.core.config import Settings
from app.core.logging import add_correlation_id
from app.infrastructure.database.engine import create_db_engine
from app.infrastructure.external_providers.tickets import FakeTicketProvider
from app.infrastructure.payments import FakepayGateway
from app.modules.outbox.models import OutboxEvent
from app.modules.outbox.relay import relay_once
from app.modules.outbox.service import Consumer, EventType, emit
from app.modules.transfers.service import perform_transfer, start_transfer
from app.workers.celery_app import create_celery
from app.workers.relay import CeleryPublisher
from tests.integration.messaging import last_message, paid, sessions  # noqa: F401
from tests.integration.payments import (
    event,
    fakepay,  # noqa: F401
    paying,
    post_webhook,
)

pytestmark = pytest.mark.integration

_EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="session")
def provider() -> TracerProvider:
    """One in-memory provider for the session: OpenTelemetry lets a process set it once."""
    found = trace.get_tracer_provider()
    if isinstance(found, TracerProvider):
        return found
    created = TracerProvider()
    created.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    trace.set_tracer_provider(created)
    return created


@pytest.fixture
def spans(provider: TracerProvider) -> Iterator[InMemorySpanExporter]:
    _EXPORTER.clear()
    yield _EXPORTER
    _EXPORTER.clear()


def trace_id_of(traceparent: str) -> int:
    return int(traceparent.split("-")[1], 16)


def tracer() -> trace.Tracer:
    return trace.get_tracer("tests")


def test_a_log_line_inside_a_span_carries_its_trace_id(spans: InMemorySpanExporter) -> None:
    with tracer().start_as_current_span("work") as span:
        inside = add_correlation_id(None, "info", {"event": "x"})
    outside = add_correlation_id(None, "info", {"event": "x"})

    assert inside["correlation_id"] == format(span.get_span_context().trace_id, "032x")
    assert "correlation_id" not in outside


def test_tracing_is_off_unless_switched_on() -> None:
    before = trace.get_tracer_provider()

    assert telemetry.configure_tracing(Settings(_env_file=None), telemetry.API) is False
    assert trace.get_tracer_provider() is before  # no provider, so no exporter either


@pytest.fixture
def traced(db_app: FastAPI, spans: InMemorySpanExporter) -> FastAPI:
    telemetry.instrument_app(db_app)
    return db_app


def test_a_webhook_stores_its_trace_with_the_event_it_emits(
    traced: FastAPI,
    api: TestClient,
    db_session: Session,
    fakepay: Any,  # noqa: F811
    spans: InMemorySpanExporter,
) -> None:
    _, order, intent = paying(api, db_session)
    spans.clear()
    assert post_webhook(api, event(intent)).status_code == 200

    stored = db_session.scalars(
        select(OutboxEvent).where(OutboxEvent.event_type == EventType.PAYMENT_SUCCEEDED)
    ).one()
    server = [s for s in spans.get_finished_spans() if s.kind == SpanKind.SERVER]
    assert server, [s.name for s in spans.get_finished_spans()]
    assert trace_id_of(stored.headers["traceparent"]) == server[-1].context.trace_id


def _emit(db: Session, headers: dict[str, Any] | None = None) -> OutboxEvent:
    event_id = emit(
        db,
        aggregate_type="test",
        aggregate_id=uuid.uuid4(),
        event_type=EventType.PAYMENT_SUCCEEDED,
        payload={},
    )
    row = db.get_one(OutboxEvent, event_id)
    if headers is not None:
        row.headers = headers
    db.commit()
    return row


class Watching:
    """A publisher that notes the trace each message is published in."""

    def __init__(self) -> None:
        self.seen: list[int] = []

    def publish(self, consumer: Consumer, message: dict[str, Any]) -> None:
        self.seen.append(trace.get_current_span().get_span_context().trace_id)


def test_the_relay_publishes_inside_the_trace_stored_with_the_event(
    db_app: FastAPI, db_session: Session, spans: InMemorySpanExporter
) -> None:
    with tracer().start_as_current_span("request") as request:
        row = _emit(db_session)
    assert trace_id_of(row.headers["traceparent"]) == request.get_span_context().trace_id

    watching = Watching()
    relay_once(db_app.state.session_factory, watching)

    assert set(watching.seen) == {request.get_span_context().trace_id}
    publish = [s for s in spans.get_finished_spans() if s.kind == SpanKind.PRODUCER]
    assert publish and publish[0].parent is not None
    assert publish[0].parent.span_id == request.get_span_context().span_id


@pytest.mark.parametrize("headers", [{}, {"traceparent": "not-a-traceparent"}, {"x": 1}])
def test_an_event_with_no_trace_or_a_broken_one_starts_its_own(
    db_app: FastAPI,
    db_session: Session,
    spans: InMemorySpanExporter,
    headers: dict[str, Any],
) -> None:
    _emit(db_session, headers)
    watching = Watching()

    report = relay_once(db_app.state.session_factory, watching)

    assert report.published == 1
    assert watching.seen and all(seen != 0 for seen in watching.seen)


@pytest.fixture
def celery_traced(provider: TracerProvider) -> Iterator[None]:
    instrumentor = CeleryInstrumentor()
    instrumentor.instrument()
    yield
    instrumentor.uninstrument()


def test_the_celery_message_carries_the_trace_to_the_worker(
    test_settings: Settings, celery_traced: None, spans: InMemorySpanExporter
) -> None:
    app = create_celery(test_settings.model_copy(update={"broker_url": "memory://"}))
    sent: list[dict[str, Any]] = []

    def note(headers: dict[str, Any] | None = None, **_: Any) -> None:
        sent.append(dict(headers or {}))

    before_task_publish.connect(note, weak=False)
    try:
        with tracer().start_as_current_span("outbox publish") as span:
            CeleryPublisher(app).publish(Consumer.NOTIFY, {"id": str(uuid.uuid4())})
    finally:
        before_task_publish.disconnect(note)

    assert sent and "traceparent" in sent[0]
    assert trace_id_of(sent[0]["traceparent"]) == span.get_span_context().trace_id


def test_a_gateway_call_is_a_client_span_and_passes_the_trace_on(
    spans: InMemorySpanExporter,
) -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(404)

    gateway = FakepayGateway(
        httpx.Client(base_url="http://fakepay", transport=httpx.MockTransport(answer))
    )
    with tracer().start_as_current_span("request") as request:
        assert gateway.by_transaction("txn_123") is None

    assert trace_id_of(seen[0].headers["traceparent"]) == request.get_span_context().trace_id
    client = [s for s in spans.get_finished_spans() if s.kind == SpanKind.CLIENT]
    assert [s.name for s in client] == ["fakepay GET /v1/intents/{transaction_id}"]
    assert "txn_123" not in client[0].name


def test_a_failing_call_marks_its_span_as_the_failing_step(spans: InMemorySpanExporter) -> None:
    with pytest.raises(RuntimeError), telemetry.client_span("ticket provider transfer"):
        raise RuntimeError("refused")

    (failed,) = spans.get_finished_spans()
    assert isinstance(failed, ReadableSpan)
    assert failed.status.status_code == StatusCode.ERROR
    assert failed.events and failed.events[0].name == "exception"


@pytest.mark.parametrize(
    ("mode", "status"), [("succeed", StatusCode.UNSET), ("permanent", StatusCode.ERROR)]
)
def test_a_refused_transfer_is_the_red_span_of_its_trace(
    sessions: Any,  # noqa: F811
    spans: InMemorySpanExporter,
    mode: Any,
    status: StatusCode,
) -> None:
    _, paid_message = paid(sessions)
    with sessions() as db, db.begin():
        start_transfer(db, uuid.UUID(paid_message["id"]), paid_message["payload"])
    with sessions() as db:
        initiated = last_message(db, "transfer.initiated")
    spans.clear()

    perform_transfer(
        sessions, FakeTicketProvider(mode), uuid.UUID(initiated["id"]), initiated["payload"]
    )

    (call,) = [s for s in spans.get_finished_spans() if s.name == "ticket provider transfer"]
    assert call.kind == SpanKind.CLIENT
    assert call.status.status_code == status


@pytest.fixture
def traced_engine(test_settings: Settings, provider: TracerProvider) -> Iterator[Engine]:
    engine = create_db_engine(test_settings)
    telemetry.instrument_engine(engine)
    telemetry.instrument_engine(engine)  # a second call adds nothing
    yield engine
    engine.dispose()


def test_each_sql_statement_is_a_span_without_its_values(
    traced_engine: Engine, spans: InMemorySpanExporter
) -> None:
    with tracer().start_as_current_span("request"), traced_engine.connect() as connection:
        connection.execute(text("SELECT :value"), {"value": "a-secret-value"})

    statements = [s for s in spans.get_finished_spans() if s.name.startswith("SELECT")]
    assert statements, [s.name for s in spans.get_finished_spans()]
    (statement,) = statements
    assert statement.kind == SpanKind.CLIENT
    assert "a-secret-value" not in str(dict(statement.attributes or {}))


def test_a_statement_outside_any_trace_starts_none(
    traced_engine: Engine, spans: InMemorySpanExporter
) -> None:
    # The relay's poll every second, a scrape, a health check: a trace each would bury the
    # purchases in Jaeger under thousands of one-span traces a day.
    with traced_engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        connection.commit()

    assert not spans.get_finished_spans()


def test_a_failing_statement_is_a_red_span(
    traced_engine: Engine, spans: InMemorySpanExporter
) -> None:
    with (
        tracer().start_as_current_span("task"),
        pytest.raises(DBAPIError),
        traced_engine.connect() as connection,
    ):
        connection.execute(text("SELECT 1 / 0"))

    (statement,) = [s for s in spans.get_finished_spans() if s.name.startswith("SELECT")]
    assert statement.status.status_code == StatusCode.ERROR
    assert statement.events and statement.events[0].name == "exception"


@pytest.mark.parametrize("violation", ["unique", "check"])
def test_a_failed_statement_sends_no_value_from_the_row(
    traced_engine: Engine, spans: InMemorySpanExporter, violation: str
) -> None:
    # Postgres names the values in its message: "Key (email)=(...) already exists" for a
    # unique key, "Failing row contains (...)" for a check. Jaeger must get neither.
    email = f"someone-{uuid.uuid4().hex[:8]}-{violation}@example.in"
    with tracer().start_as_current_span("request"), traced_engine.connect() as connection:
        connection.execute(
            text("CREATE TEMP TABLE people (email text UNIQUE CHECK (email NOT LIKE '%-check@%'))")
        )
        if violation == "unique":
            connection.execute(text("INSERT INTO people VALUES (:email)"), {"email": email})
        with pytest.raises(DBAPIError) as raised:
            connection.execute(text("INSERT INTO people VALUES (:email)"), {"email": email})
    assert email in str(raised.value)  # the message did carry it

    (failed,) = [s for s in spans.get_finished_spans() if s.status.status_code == StatusCode.ERROR]
    sent = str(
        [failed.status.description, dict(failed.attributes or {})]
        + [dict(e.attributes or {}) for e in failed.events]
    )
    assert email not in sent
    assert "Failing row" not in sent and "Key (" not in sent
    # What failed is still plain: the error's class and its SQLSTATE.
    sqlstate = "23505" if violation == "unique" else "23514"
    assert failed.status.description and sqlstate in failed.status.description
    assert (failed.attributes or {})["db.response.status_code"] == sqlstate
