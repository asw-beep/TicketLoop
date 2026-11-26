"""Traces: one purchase followed across the API, the outbox, RabbitMQ and the worker.

Off unless TRACING_ENABLED=true. Off, no exporter is made and every span below is
OpenTelemetry's no-op one: the code paths are the same and cost next to nothing. On, spans go in
batches from a background thread to an OTLP endpoint (Jaeger), so a slow or missing Jaeger
never holds up a request; it only means those spans are lost.

How a trace crosses the queue:

1. A request (or a worker task) calls `emit()`, which stores `current_context_headers()` in
   the outbox row: `{"traceparent": "00-<trace id>-<span id>-01"}`.
2. The relay publishes the row inside `continued(row.headers, ...)`, a span in that trace.
3. Celery's instrumentation puts the context into the task message's headers, and the
   instrumented worker runs the task inside it.

An event emitted outside any trace (a beat job) has empty headers and starts a trace of its
own when published; headers that are not a trace context are treated the same way.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI
from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.util.types import AttributeValue
from sqlalchemy import Connection, Engine, event
from sqlalchemy.engine import ExceptionContext, ExecutionContext

from app.core.config import Settings

API = "ticketloop-api"
WORKER = "ticketloop-worker"
RELAY = "ticketloop-relay"

EXPORT_TIMEOUT_SECONDS = 2
# Prometheus asks for /metrics every few seconds, and the health check is polled: neither is
# a story worth a trace.
UNTRACED_URLS = "/metrics$,/api/v1/health$"

_tracer = trace.get_tracer("ticketloop")


def configure_tracing(settings: Settings, service: str) -> bool:
    """Send this process's spans to `settings.otlp_endpoint`, named `service`. Returns whether
    tracing is on. A process gets one tracer provider; a second call keeps the first."""
    if not settings.tracing_enabled:
        return False
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return True
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    exporter = OTLPSpanExporter(
        endpoint=f"{settings.otlp_endpoint.rstrip('/')}/v1/traces",
        timeout=EXPORT_TIMEOUT_SECONDS,
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return True


def instrument_app(app: FastAPI) -> None:
    """A SERVER span per request, continuing a `traceparent` the caller sent."""
    FastAPIInstrumentor.instrument_app(
        app, excluded_urls=UNTRACED_URLS, exclude_spans=["receive", "send"]
    )


def instrument_engine(engine: Engine) -> None:
    """A CLIENT span per SQL statement run inside a trace (a request, a task, a publish),
    named like `SELECT ticketloop`, with the statement's text and its placeholders, never the
    values. A statement outside any trace (the relay's poll, a health check) starts none. A
    failed statement's span is an error, with the error's class and SQLSTATE, never its text.

    Hand-made on SQLAlchemy's cursor events rather than OpenTelemetry's SQLAlchemy
    instrumentation: that one supports SQLAlchemy below 2.1, and on 2.1 it fails while
    instrumenting, importing the asyncio extension, which needs greenlet."""
    if not event.contains(engine, "before_cursor_execute", _statement_started):
        event.listen(engine, "before_cursor_execute", _statement_started)
        event.listen(engine, "after_cursor_execute", _statement_finished)
        event.listen(engine, "handle_error", _statement_failed)


def _statement_started(
    connection: Connection,
    _cursor: Any,
    statement: str,
    _parameters: Any,
    context: ExecutionContext | None,
    _executemany: bool,
) -> None:
    if context is None or not trace.get_current_span().get_span_context().is_valid:
        return
    words = statement.split(None, 1)
    database = connection.engine.url.database or ""
    span = _tracer.start_span(
        f"{words[0].upper() if words else 'SQL'} {database}".strip(),
        kind=SpanKind.CLIENT,
        attributes={
            "db.system.name": connection.dialect.name,
            "db.namespace": database,
            "db.query.text": statement[:4000],
        },
    )
    context._ticketloop_span = span  # type: ignore[attr-defined]


def _statement_finished(
    _connection: Connection,
    _cursor: Any,
    _statement: str,
    _parameters: Any,
    context: ExecutionContext | None,
    _executemany: bool,
) -> None:
    span = getattr(context, "_ticketloop_span", None)
    if span is not None:
        span.end()


def _statement_failed(exception_context: ExceptionContext) -> None:
    span = getattr(exception_context.execution_context, "_ticketloop_span", None)
    if span is None:
        return
    error = exception_context.original_exception
    # Postgres's message can quote the row: "Key (email)=(...) already exists", "Failing row
    # contains (...)". So the span gets what kind of failure it was, and the logs the detail.
    sqlstate = getattr(error, "sqlstate", None)
    summary = f"{type(error).__name__} (SQLSTATE {sqlstate})" if sqlstate else type(error).__name__
    if sqlstate:
        span.set_attribute("db.response.status_code", sqlstate)
    span.add_event(
        "exception",
        {
            "exception.type": f"{type(error).__module__}.{type(error).__qualname__}",
            "exception.message": summary,
        },
    )
    span.set_status(Status(StatusCode.ERROR, summary))
    span.end()


def current_context_headers() -> dict[str, str]:
    """The current trace context as headers, to store or send on; empty outside a trace."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


@contextmanager
def continued(
    headers: Any, name: str, attributes: Mapping[str, AttributeValue] | None = None
) -> Iterator[trace.Span]:
    """A PRODUCER span in the trace the stored `headers` name, or in a new trace when they
    name none. Headers that are not a mapping of strings are ignored, never an error."""
    carrier = (
        {str(key): value for key, value in headers.items() if isinstance(value, str)}
        if isinstance(headers, Mapping)
        else {}
    )
    with _tracer.start_as_current_span(
        name, context=propagate.extract(carrier), kind=SpanKind.PRODUCER, attributes=attributes
    ) as span:
        yield span


@contextmanager
def client_span(
    name: str, attributes: Mapping[str, AttributeValue] | None = None
) -> Iterator[trace.Span]:
    """A CLIENT span for a call to another system. An exception that leaves it is recorded on
    it and marks it as an error: in Jaeger, the failing step is the red one."""
    with _tracer.start_as_current_span(name, kind=SpanKind.CLIENT, attributes=attributes) as span:
        yield span
