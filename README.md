# TicketLoop

A FastAPI backend for fixed-price ticket resale, built to stay correct when 100 buyers hit one ticket at once, when requests are retried, when webhooks arrive twice, and when the message broker goes down.

The specification is [`plan.md`](plan.md). The build order and study plan is [`build-and-learn-plan.md`](build-and-learn-plan.md).

## Status

| Module | State |
| --- | --- |
| M0 · Foundations and design | Done: see [Design docs](#design-docs) |
| M1 · Dev environment and skeleton | Done: app factory, `/api/v1/health` with a database check, settings, JSON logs with request IDs, RFC 9457 errors, Docker Compose, tests |
| M2 · Database layer | Next |

## Run it

You need Docker Desktop, Python 3.12 or newer, and [uv](https://docs.astral.sh/uv/).

**Everything in containers** (what the M1 checkpoint tests):

```bash
docker compose up --build
curl localhost:8000/api/v1/health
# {"status":"ok","checks":{"database":"ok"}}
```

**Day to day** (Postgres in Docker, the API on Windows with auto-reload):

```bash
docker compose up -d db               # only Postgres
cp .env.example .env                  # optional; the defaults already match compose
uv sync                               # create .venv and install dependencies
uv run uvicorn app.main:app --reload  # http://localhost:8000/docs for the API explorer
```

Set `LOG_JSON=false` in `.env` for coloured, readable log lines while developing. Stop the `api` container first (`docker compose stop api`) if it is running, since both use port 8000.

```bash
docker compose down      # stop everything; database data survives in the pgdata volume
docker compose down -v   # stop and delete the database data too
```

## Test and lint

```bash
uv run pytest             # integration tests are skipped unless Postgres is running
uv run pytest -m integration
uv run ruff check .
uv run ruff format .
```

## Layout

```text
app/
  main.py                  app factory; uvicorn runs app.main:app
  api/                     /api/v1 router and the health check
  core/                    config, logging, request-ID middleware, error format
  modules/                 one package per business module (M3 onward)
  infrastructure/          Postgres engine, later Redis, RabbitMQ, storage, fake providers
  workers/                 background jobs (M8 onward)
tests/
  unit/                    fast, no external services
  integration/             need Postgres
  concurrency/ failure/ load/   later modules
docs/                      design documents (below)
documentation/             one HTML page per finished module for revision and interview prep; open index.html
docker-compose.yml         Postgres + API
Dockerfile                 the API image
```

`migrations/` does not exist yet: `alembic init migrations` creates it in M2.

## Design docs

| Document | What it answers |
| --- | --- |
| [`docs/requirements.md`](docs/requirements.md) | Every FR and NFR as checkable rules, with the test that proves each |
| [`docs/database-design.md`](docs/database-design.md) | ER diagrams, which constraint enforces which rule, indexes, changes from `plan.md` |
| [`docs/schema-draft.sql`](docs/schema-draft.sql) | The full target DDL; loads cleanly into Postgres 17 |
| [`docs/schema-checks.sql`](docs/schema-checks.sql) | Statements that should be rejected by the schema's constraints |
| [`docs/state-machines.md`](docs/state-machines.md) | Listing, interest, reservation, order, payment and transfer lifecycles |
| [`docs/api-contract.md`](docs/api-contract.md) | Every endpoint: who may call it, request, response, errors |
| [`docs/hard-problems.md`](docs/hard-problems.md) | The six hard problems as exact SQL, with crash analysis |
| [`docs/purchase-flow.md`](docs/purchase-flow.md) | The end-to-end purchase and what every table holds at each step |
