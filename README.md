# TicketLoop

[![CI](https://github.com/asw-beep/TicketLoop/actions/workflows/ci.yml/badge.svg)](https://github.com/asw-beep/TicketLoop/actions/workflows/ci.yml)

A FastAPI backend for fixed-price ticket resale, built to stay correct when 100 buyers hit one ticket at once, when requests are retried, when webhooks arrive twice, and when the message broker goes down.

The specification is [`plan.md`](plan.md). The build order and study plan is [`build-and-learn-plan.md`](build-and-learn-plan.md).

## Status

| Module | State |
| --- | --- |
| M0 · Foundations and design | Done: see [Design docs](#design-docs) |
| M1 · Dev environment and skeleton | Done: app factory, `/api/v1/health` with a database check, settings, JSON logs with request IDs, RFC 9457 errors, Docker Compose, tests |
| M2 · Database layer | Done: users, events, tickets, listings and audit log by Alembic migration; constraints that enforce the business rules; one transaction per request; a test database rolled back after every test |
| M3 · Identity and auth | Done: register and login with Argon2id; 15-minute JWT access tokens; 7-day refresh tokens that rotate, with reuse detection that revokes the whole session; logout; password change and reset; `get_current_user` and `require_admin`; `GET` and `PATCH /users/me`; CI on every push |
| M4 · Events, tickets and document storage | Next |

## Run it

You need Docker Desktop, Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Node 24 LTS for the frontend.

**Everything in containers** (Postgres, Mailpit, then a one-off `migrate` job, then the API):

```bash
docker compose up --build
curl localhost:8000/api/v1/health
# {"status":"ok","checks":{"database":"ok"}}
```

**Day to day** (Postgres and Mailpit in Docker, the API and the frontend on Windows, both reloading as you edit):

```bash
docker compose up -d db mailpit       # Postgres, and a fake inbox at http://localhost:8025
cp .env.example .env                  # optional; the defaults already match compose
uv sync                               # create .venv and install dependencies
uv run alembic upgrade head           # create or update the tables
uv run uvicorn app.main:app --reload  # http://localhost:8000/docs for the API explorer
```

Then, in a second terminal:

```bash
cd frontend
npm install
npm run dev                           # http://localhost:5173
```

The dev server forwards `/api` to the API on port 8000, so the browser sees one origin.

Set `LOG_JSON=false` in `.env` for coloured, readable log lines while developing. Stop the `api` container first (`docker compose stop api`) if it is running, since both use port 8000.

```bash
docker compose down      # stop everything; database data survives in the pgdata volume
docker compose down -v   # stop and delete the database data too
```

## Try the auth flow

Open http://localhost:5173: create an account, change your name, log out and in, and use **Forgot your password?**. The reset email lands in Mailpit at http://localhost:8025, and its link opens the reset page.

From a terminal instead (`-c` and `-b` keep the refresh cookie in a file, as a browser would):

```bash
B=localhost:8000/api/v1
curl -s $B/auth/register -H 'content-type: application/json' \
  -d '{"email":"asha@example.in","password":"correct horse battery staple","full_name":"Asha"}'
curl -s -c jar.txt $B/auth/login -H 'content-type: application/json' \
  -d '{"email":"asha@example.in","password":"correct horse battery staple"}'
# {"access_token":"eyJ...","token_type":"bearer","expires_in":900}  plus the refresh cookie
curl -s $B/users/me -H "Authorization: Bearer <access_token>"
curl -s -b jar.txt -c jar.txt -X POST $B/auth/refresh   # a new access token; the cookie rotates
```

Or open http://localhost:8000/docs, log in, and paste the access token into **Authorize**.

No route grants the admin role. Promote a registered account from the command line:

```bash
uv run python -m scripts.make_admin asha@example.in            # --revoke to undo
```

Set `JWT_SECRET_KEY` (at least 32 bytes) anywhere but your laptop; staging and production refuse to start with the built-in development key.

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
REQUIRE_POSTGRES=1 uv run pytest   # fail instead of skip without Postgres, as CI does
uv run ruff check .
uv run ruff format .
```

The frontend, from `frontend/`:

```bash
npm test                  # unit tests (Vitest)
npm run e2e               # browser tests in your Chrome; needs `docker compose up -d db mailpit`
npm run typecheck && npm run lint && npm run format:check
npm run api:types         # after changing the API: regenerate openapi.json and the TypeScript types
```

## Layout

```text
app/
  main.py                  app factory; uvicorn runs app.main:app
  api/                     /api/v1 router and the health check
  core/                    config, logging, request-ID middleware, error format, security (hashing, JWT)
  models.py                imports every model, so Alembic sees every table
  modules/                 one package per business module: models, schemas, service, router
    identity/              users, tokens, auth routes, get_current_user and require_admin
  infrastructure/          Postgres engine, sessions, base model, email sender; later Redis, RabbitMQ, storage
  workers/                 background jobs (M8 onward)
tests/
  unit/                    fast, no external services
  integration/             need Postgres
  concurrency/ failure/ load/   later modules
migrations/                Alembic env.py and one file per schema change
scripts/                   experiments and operator tools: lock_experiment, make_admin, export_openapi
frontend/                  React + TypeScript app (Vite); each module adds its screens
  src/api/                 typed client; schema.d.ts is generated from the backend
  src/auth/                access token in memory, refresh through the HttpOnly cookie
  src/pages/               one file per screen
  e2e/                     Playwright browser tests against the real stack
.github/workflows/ci.yml   backend tests, frontend checks and browser tests on every push
docs/                      design documents (below)
documentation/             one HTML page per finished module for revision and interview prep; open index.html
docker-compose.yml         Postgres, Mailpit, the migrate job, the API
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
