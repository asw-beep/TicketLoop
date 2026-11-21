# TicketLoop

A FastAPI backend for fixed-price ticket resale, built to stay correct when 100 buyers hit one ticket at once, when requests are retried, when webhooks arrive twice, and when the message broker goes down.

The specification is [`plan.md`](plan.md). The build order and study plan is [`build-and-learn-plan.md`](build-and-learn-plan.md).

## Status

| Module | State |
| --- | --- |
| M0 · Foundations and design | Done: see [Design docs](#design-docs) |
| M1 · Dev environment and skeleton | Done: app factory, `/api/v1/health` with a database check, settings, JSON logs with request IDs, RFC 9457 errors, Docker Compose, tests |
| M2 · Database layer | Done: users, events, tickets, listings and audit log by Alembic migration; constraints that enforce the business rules; one transaction per request; a test database rolled back after every test |
| M3 · Identity and auth | Next |

## Run it

You need Docker Desktop, Python 3.12 or newer, and [uv](https://docs.astral.sh/uv/).

**Everything in containers** (Postgres, then a one-off `migrate` job, then the API):

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
uv run alembic upgrade head           # create or update the tables
uv run uvicorn app.main:app --reload  # http://localhost:8000/docs for the API explorer
```

Set `LOG_JSON=false` in `.env` for coloured, readable log lines while developing. Stop the `api` container first (`docker compose stop api`) if it is running, since both use port 8000.

```bash
docker compose down      # stop everything; database data survives in the pgdata volume
docker compose down -v   # stop and delete the database data too
```

## Migrations

```bash
uv run alembic revision --autogenerate -m "what changed"   # draft a migration from the models
uv run alembic upgrade head --sql      # read the SQL before running it
uv run alembic upgrade head
uv run alembic downgrade -1            # undo the last one
uv run alembic check                   # fails if the models and migrations disagree
```

Autogenerate cannot see triggers, functions or a change to an existing CHECK. Read every draft and write those parts by hand.

## Test and lint

```bash
uv run pytest             # integration tests are skipped unless Postgres is running
                          # they build ticketloop_test from the migrations each run
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
  models.py                imports every model, so Alembic sees every table
  modules/                 one package per business module; each owns its models.py
  infrastructure/          Postgres engine, sessions, base model; later Redis, RabbitMQ, storage, fake providers
  workers/                 background jobs (M8 onward)
tests/
  unit/                    fast, no external services
  integration/             need Postgres
  concurrency/ failure/ load/   later modules
migrations/                Alembic env.py and one file per schema change
scripts/                   one-off experiments, e.g. python -m scripts.lock_experiment
docs/                      design documents (below)
documentation/             one HTML page per finished module for revision and interview prep; open index.html
docker-compose.yml         Postgres, the migrate job, the API
Dockerfile                 the API image
```

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
