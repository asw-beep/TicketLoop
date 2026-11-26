# M11 Observability and delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Metrics, traces and logs that let one failed purchase be found by its order id, opened as a trace that crosses RabbitMQ, and its failing step pointed at; and a CI that builds the image and fails on known vulnerabilities.

**Architecture:** `prometheus-client` counters in the code paths that matter, scrape-time collectors for the outbox backlog, the connection pool and the M10 Redis counters, and `prometheus-fastapi-instrumentator` for per-route HTTP metrics on `/metrics`. OpenTelemetry for FastAPI, SQLAlchemy and Celery, exported over OTLP to Jaeger; `emit()` stores the current trace context in the outbox row's `headers`, the relay publishes each event inside a span that continues it, and Celery's instrumentation carries it to the worker. The trace id goes into every log line as `correlation_id`. Prometheus, Grafana (provisioned dashboard) and Jaeger in Compose.

**Tech Stack:** prometheus-client 0.26.0, prometheus-fastapi-instrumentator 8.1.0, opentelemetry-sdk 1.45.2 and instrumentations 0.66b2, OTLP over HTTP; prom/prometheus v3.7.3, grafana/grafana 12.2.1, jaegertracing/jaeger 2.11.0; Bandit, pip-audit, npm audit.

**Spec:** `docs/superpowers/specs/2026-11-23-slim-roadmap-m6-m12-design.md`, section "M11 · Observability and delivery".

## Global Constraints

- Labels stay low-cardinality: never a user, listing, order or any other id, never a raw path.
- Tracing is off unless `TRACING_ENABLED=true`; off, nothing is exported and nothing is slower. Jaeger down never fails or slows a request.
- No screens. Grafana is this module's screen.
- Ruff line length 100; services never commit; problem+json errors.
- CI fails on a high-severity Bandit finding, any known vulnerability pip-audit reports (it gives no severities), and `npm audit --audit-level=high`.

## Review Focus

1. A label that could take a caller-supplied value: an unknown path, a bad problem code, an event type from a forged webhook. Expect a fixed, small set of values.
2. Tracing off, or Jaeger unreachable: requests, the relay and the worker behave and perform as before; no exporter errors in the request path.
3. Events emitted outside any trace (beat jobs, the sweep) or carrying an old or malformed `headers`: published as their own trace, never an error.
4. The worker's prefork children: their counters add up on one endpoint, and a restarted worker starts from a clean multiprocess directory.
5. `/metrics` while Postgres is down: it still answers, without the backlog gauge, and quickly.

## Rulings made in advance

- **httpx2 is not httpx.** The OpenTelemetry httpx instrumentation patches the `httpx` module and imports it; the gateway client uses `httpx2`. The gateway gets a hand-written CLIENT span and injects `traceparent` into its requests.
- **Worker retries are one counter with an outcome**: `worker_tasks_total{task, outcome}` with `done`, `retry`, `rejected`. Retries are `outcome="retry"`.
- **The outbox backlog is measured by the API at scrape time** (`count(*)` and the oldest `created_at` of unpublished rows, with a 1-second statement timeout), not by the relay: any number of relays may run.
- **Worker metrics in multiprocess mode** (`PROMETHEUS_MULTIPROC_DIR`, emptied at worker start, served by the main process on port 9100). The relay is one process and serves its own on 9101. Beat has none.
- **`/metrics` is outside `/api/v1`** and unauthenticated, as the spec names it. In a deployment it sits behind the network edge; the docs say so.
- **The trace crosses RabbitMQ through Celery's own instrumentation:** the relay starts `outbox.publish` under the context stored in the row; the instrumented `send_task` injects that into the Celery message; the instrumented worker continues it.
- **Logs bind the event's ids in the worker** (`event_id`, and `order_id`, `transfer_id`, `payment_id`, `reservation_id`, `listing_id` when the payload has them), so a worker's every line names its order.
- **Tests:** tracing tests use one in-memory tracer provider set globally for the test session; metrics tests read the default registry's text output.

---

### Task 1: Metrics

**Files:** Create `app/core/metrics.py`; modify `app/main.py`, `app/modules/reservations/router.py`, `app/modules/payments/router.py` (webhook), `app/workers/tasks.py`, `app/workers/celery_app.py`, `app/workers/relay.py`, `app/core/config.py`. Test: `tests/integration/test_metrics.py`, `tests/unit/test_metrics.py`.

**Interfaces — Produces:** `RESERVATION_ATTEMPTS` (`reservation_attempts_total{result}`: `reserved`, `already_yours`, or the problem code of the refusal from a fixed list, else `other`); `PAYMENT_EVENTS` (`payment_events_total{type, outcome}`: type one of the known event types else `unknown`, outcome `processed`, `ignored`, `failed`, `duplicate`, `rejected`); `WEBHOOK_SECONDS` (`payment_webhook_seconds`); `WORKER_TASKS` (`worker_tasks_total{task, outcome}`); `OUTBOX_PUBLISHED` (`outbox_published_total`), `OUTBOX_FAILURES` (`outbox_publish_failures_total`); collectors `outbox_backlog`, `outbox_oldest_unpublished_seconds`, `db_pool_checked_out_connections`, `cache_requests_total{outcome}`, `rate_limit_decisions_total{decision}`; `watch(app)` pointing the collectors at an app's engine and sessions; `serve_worker_metrics(port)`.

- [ ] Tests first: `/metrics` answers 200 text with `http_requests_total` labelled by route template (`/api/v1/listings/{listing_id}`), never an id; a reserve that succeeds, a repeat, and a refused one count `reserved`, `already_yours`, `listing-not-available`; a processed, a duplicate and a badly signed webhook count their outcomes and observe the histogram; `outbox_backlog` equals the unpublished rows; the pool gauge exists; the Redis counters are exported; a task that finishes, retries or is rejected counts its outcome; no line of `/metrics` contains a UUID.
- [ ] Implement, run, pass.

### Task 2: Traces and correlated logs

**Files:** Create `app/core/telemetry.py`; modify `app/core/logging.py`, `app/main.py`, `app/modules/outbox/service.py`, `app/modules/outbox/relay.py`, `app/workers/relay.py`, `app/workers/celery_app.py`, `app/workers/runtime.py`, `app/workers/tasks.py`, `app/infrastructure/payments/fakepay.py`, `app/core/config.py` (`tracing_enabled` false, `otlp_endpoint` `http://localhost:4318`, `service_name`). Test: `tests/integration/test_tracing.py`, `tests/unit/test_logging.py` additions.

**Interfaces — Produces:** `configure_tracing(settings, service) -> bool`; `instrument_app(app)`, `instrument_engine(engine)`; `current_context_headers() -> dict[str, str]`; `continued(headers, name)` context manager for a PRODUCER span under stored headers; the structlog processor `add_correlation_id`.

- [ ] Tests first: inside a span, a log line carries `correlation_id` equal to the trace id, and none outside; a request that emits an event stores a `traceparent` with the request's trace id; the relay's publish runs inside a span whose trace id is the stored one, and an event with empty or malformed headers starts its own; the Celery publish injects `traceparent` into the task's headers; the gateway's request carries `traceparent` and makes a CLIENT span; a worker task's log lines carry the payload's `order_id`; with tracing off, `configure_tracing` returns false and no exporter exists.
- [ ] Implement, run, pass.

### Task 3: Prometheus, Grafana, Jaeger in Compose

**Files:** `docker-compose.yml`; create `observability/prometheus.yml`, `observability/grafana/provisioning/datasources/datasources.yml`, `observability/grafana/provisioning/dashboards/dashboards.yml`, `observability/grafana/dashboards/ticketloop.json`. Test: `tests/unit/test_compose.py`, `tests/unit/test_observability_config.py`.

- [ ] Tests first: compose has `prometheus`, `grafana`, `jaeger` with exact image tags and the config mounted; api, worker and relay set `TRACING_ENABLED` and the OTLP endpoint; Prometheus scrapes api:8000, worker:9100, relay:9101 and RabbitMQ's per-object metrics; the dashboard JSON parses and every custom metric name from Task 1 appears in a panel, with p50, p95 and p99 per route.
- [ ] Implement, run, pass; bring the stack up and check every target is up.

### Task 4: Delivery in CI

**Files:** `.github/workflows/ci.yml`. Test: `tests/unit/test_ci.py`.

- [ ] Tests first: the workflow has an image job that builds the Dockerfile, runs it against a Postgres service as uid 10001 and waits for `/api/v1/health` 200; and a security job running Bandit (high severity), pip-audit on the locked runtime requirements, and `npm audit --audit-level=high`.
- [ ] Run each tool locally first and fix what it finds.

### Task 5: Docs and ship

README (observability: where each UI is, the walk from an order id to a trace), api-contract (`/metrics`), the documentation page with the failed-purchase walkthrough and Grafana and Jaeger screenshots, commit, push, CI.
