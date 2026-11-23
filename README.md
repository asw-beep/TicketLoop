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
| M4 · Events, tickets and document storage | Done: admin event management with a status machine; ticket registration; proof of purchase in MinIO (S3) behind 5-minute signed links, typed by its first bytes; admin verification; one 404 for "missing" and "not yours" on every ticket route, proven by a test that enumerates them; screens for all of it |
| M5 · Listings, verification and audit log | Done: listing a verified ticket at no more than face value; admin review with no self-review; one state machine whose every move is a conditional `UPDATE` plus an audit row in the same transaction; listing history; the event-cancel cascade and listing expiry; races between admins proven with real concurrent transactions; screens for all of it |
| M6 · Discovery: search, filters, pagination | Next |

## Run it

You need Docker Desktop, Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Node 24 LTS for the frontend.

**Everything in containers** (Postgres, Mailpit, MinIO, then a one-off `migrate` job, then the API):

```bash
docker compose up --build
curl localhost:8000/api/v1/health
# {"status":"ok","checks":{"database":"ok"}}
```

**Day to day** (Postgres, Mailpit and MinIO in Docker, the API and the frontend on Windows, both reloading as you edit):

```bash
docker compose up -d db mailpit minio # Postgres, a fake inbox at http://localhost:8025, and S3 storage
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

MinIO stores the uploaded proof of purchase. Its web console is at http://localhost:9001 (user `ticketloop`, password `ticketloop-local-secret`); the files sit in the bucket `ticketloop-documents`, which the API creates on startup when `ENVIRONMENT=local`. Anywhere else, create the bucket yourself and set `STORAGE_ENDPOINT_URL`, `STORAGE_PUBLIC_URL` (the host browsers reach, used to sign download links), `STORAGE_BUCKET`, `STORAGE_ACCESS_KEY`, `STORAGE_SECRET_KEY` and `STORAGE_REGION`; see `.env.example`. Staging and production refuse to start with the built-in storage secret.

Set `LOG_JSON=false` in `.env` for coloured, readable log lines while developing. Stop the `api` container first (`docker compose stop api`) if it is running, since both use port 8000.

```bash
docker compose down      # stop everything; data survives in the pgdata and miniodata volumes
docker compose down -v   # stop and delete the database and the stored files too
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

## Try the ticket flow

1. Create two accounts at http://localhost:5173/register, one for you as admin and one as a ticket holder (a private window keeps the sessions apart).
2. Promote the first: `uv run python -m scripts.make_admin <email>`, then reload the page. **Events**, **Verify tickets** and **Review listings** appear in the top bar.
3. **Events → New event**: fill it in with a start time in the future, create it (it starts as a draft), then **Publish**.
4. As the ticket holder, **My tickets → Register a ticket**: pick the event, enter the category, the price you paid in rupees, and the order and ticket numbers.
5. On the ticket's page, upload a PDF, PNG or JPEG up to 5 MB, then **Open** it: the file comes straight from MinIO through a link that works for 5 minutes.
6. As the admin, **Verify tickets**: open the same file, then **Verify** (with or without "Transfer eligible") or **Reject** with a reason. The holder's ticket page shows the decision.

The same from a terminal (it uses [`jq`](https://jqlang.org/) to pick fields out of the JSON), with `$ADMIN` and `$HOLDER` holding access tokens from `/auth/login`:

```bash
B=localhost:8000/api/v1
PROVIDER=$(curl -s $B/admin/providers -H "Authorization: Bearer $ADMIN" | jq -r '.[0].id')
EVENT=$(curl -s $B/admin/events -H "Authorization: Bearer $ADMIN" -H 'content-type: application/json' \
  -d "{\"name\":\"Monsoon Beats\",\"category\":\"CONCERT\",\"venue\":\"Stadium\",\"city\":\"Kochi\",
       \"starts_at\":\"2027-01-15T13:00:00Z\",\"organizer\":\"Backwater Live\",\"provider_id\":\"$PROVIDER\"}" | jq -r .id)
curl -s -X PATCH $B/admin/events/$EVENT -H "Authorization: Bearer $ADMIN" \
  -H 'content-type: application/json' -d '{"status":"PUBLISHED"}'
TICKET=$(curl -s $B/tickets -H "Authorization: Bearer $HOLDER" -H 'content-type: application/json' \
  -d "{\"event_id\":\"$EVENT\",\"ticket_category\":\"GENERAL\",\"original_price_paise\":250000,
       \"purchase_reference\":\"ORD-1\",\"provider_reference\":\"TKT-1\"}" | jq -r .id)
DOC=$(curl -s $B/tickets/$TICKET/documents -H "Authorization: Bearer $HOLDER" -F file=@receipt.pdf | jq -r .id)
curl -s $B/tickets/$TICKET/documents/$DOC -H "Authorization: Bearer $HOLDER"   # {"url": "...", "expires_at": "..."}
curl -s $B/admin/tickets/$TICKET/verify -H "Authorization: Bearer $ADMIN" \
  -H 'content-type: application/json' -d '{"decision":"VERIFIED","transfer_eligible":true}'
```

## Try the listing flow

Carry on from the ticket flow, with the ticket verified as **Transfer eligible**.

1. As the holder, open the ticket's page. Under **Sell this ticket**, enter an asking price no higher than what you paid and press **Submit for review** (or **Save draft** to submit later from the listing's page).
2. The listing's page shows **Pending review**. Change the price there while you wait if you like.
3. As the admin, **Review listings**: check the listing against **View the ticket and its evidence**, then **Approve**, or **Reject** with a reason the holder will see. An admin cannot review their own listing, so if your admin account is also the seller, approve it from a second admin account.
4. Back as the holder, the listing is **Active**, and **History** reads: listed as a draft, submitted for review, approved by an admin. Anyone else opening the listing's address sees the ticket and the price, but no history and no controls; a draft or pending listing is "Not found" to them.
5. **My listings** has every listing in every status. **Review listings → All listings** shows an admin everyone's.

From a terminal, carrying on with the variables above:

```bash
LISTING=$(curl -s $B/listings -H "Authorization: Bearer $HOLDER" -H 'content-type: application/json' \
  -d "{\"ticket_id\":\"$TICKET\",\"asking_price_paise\":200000}" | jq -r .id)     # a DRAFT
curl -s -X POST $B/listings/$LISTING/submit -H "Authorization: Bearer $HOLDER"       # PENDING_VERIFICATION
curl -s -X POST $B/admin/listings/$LISTING/approve -H "Authorization: Bearer $ADMIN" # ACTIVE
curl -s $B/listings/$LISTING                                                         # the public view, no token
curl -s $B/listings/$LISTING/history -H "Authorization: Bearer $HOLDER" | jq '.items[].action'
```

`ACTIVE` listings whose end has passed become `EXPIRED` when the expiry job runs. Until M11 schedules it, run it by hand:

```bash
uv run python -m scripts.expire_listings   # Expired 0 listing(s).
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
REQUIRE_POSTGRES=1 uv run pytest   # fail instead of skip without Postgres, as CI does
REQUIRE_STORAGE=1 uv run pytest    # the same for the MinIO tests (docker compose up -d minio)
uv run ruff check .
uv run ruff format .
```

The frontend, from `frontend/`:

```bash
npm test                  # unit tests (Vitest)
npm run e2e               # browser tests in your Chrome; needs `docker compose up -d db mailpit minio`
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
    events/                events, ticket providers, the event status machine, admin event routes
    tickets/               ticket registration, evidence uploads and links, admin verification
    listings/              listings, the listing state machine, review, history, the event cascade and expiry
    audit/                 the append-only audit log and record(), the one way to write to it
  infrastructure/          Postgres engine, sessions, base model, email sender, object storage (S3 and in-memory); later Redis, RabbitMQ
  workers/                 background jobs (M8 onward)
tests/
  unit/                    fast, no external services
  integration/             need Postgres
  concurrency/ failure/ load/   later modules
migrations/                Alembic env.py and one file per schema change
scripts/                   experiments and operator tools: lock_experiment, make_admin, export_openapi, expire_listings
frontend/                  React + TypeScript app (Vite); each module adds its screens
  src/api/                 typed client; schema.d.ts is generated from the backend
  src/auth/                access token in memory, refresh through the HttpOnly cookie
  src/pages/               one file per screen
  e2e/                     Playwright browser tests against the real stack
.github/workflows/ci.yml   backend tests, frontend checks and browser tests on every push
docs/                      design documents (below)
documentation/             one HTML page per finished module for revision and interview prep; open index.html
docker-compose.yml         Postgres, Mailpit, MinIO, the migrate job, the API
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
