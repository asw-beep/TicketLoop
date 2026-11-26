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
| M6 · Discovery: search, filters, pagination | Done: public search with filters and sorts; keyset pagination on every list; indexes proven with `EXPLAIN ANALYZE` on 100,000 listings ([results](docs/discovery-performance.md)): page 500 in 0.2 ms against 45 ms with `OFFSET`; the front page is the search |
| M7 · Reservations and the race | Done: 10-minute holds by one conditional `UPDATE`; 100 buyers against four API processes end with exactly one winner, 20 rounds in a row; compared with `FOR UPDATE` and `SERIALIZABLE` ([design and numbers](docs/reservation-design.md)); holds expire with `SKIP LOCKED`; an event with a held ticket can't be cancelled; reserve, countdown and cancel on screen |
| M8 · Orders and payments | Done: idempotent orders at the server's price; a pretend gateway, fakepay, that can be late, silent, repeat itself or forge; HMAC-signed webhooks applied exactly once; reconciliation that repairs what a webhook never told us; a paid ticket is never released, 50 races in a row; checkout, order page and My orders on screen |
| M9 · Outbox, worker and transfer | Done: every event written in the transaction of its change and published by a relay that waits out a dead broker; RabbitMQ and Celery with late acknowledgement and dead-letter queues; consumers that do their work once however often a message arrives; the ticket moves to its buyer through an idempotent provider call, or to review; a broker outage, a killed worker and a poison message, all automated drills; the order page follows the ticket to My tickets |
| M10 · Redis: caching and rate limits | Done: search and the event page cached in Redis under versioned keys that a commit to `listings` or `events` makes unreachable, found by the session, so no write in the API or the worker can forget; 30 to 60 second jittered expiry; `X-Cache` on every cached route; token buckets in one Lua script on Redis's clock for login, password reset, reserve, payment and search, with `429` and `Retry-After`; when Redis is down every route still answers, uncached and unlimited, and only the request that finds it gone waits; a countdown on every form that can be refused |
| M11 · Observability and delivery | Done: Prometheus metrics on `/metrics` (requests, errors and p50/p95/p99 per route, reservation results, payment webhooks, worker task outcomes, the outbox backlog, cache and rate-limit counts), with no id ever in a label; OpenTelemetry traces from the API through the outbox and RabbitMQ into the worker, in Jaeger; the trace id on every log line as `correlation_id`, so one failed purchase is found by its order id, opened as one trace and its failing step is the red span; Prometheus, a provisioned Grafana dashboard and Jaeger in Compose; CI builds the image and starts it with production settings, and fails on a high-severity Bandit finding, a known vulnerability in a runtime package or a high npm advisory |
| M12 · Load testing and the numbers | Next (Mon 30 Nov) |

The roadmap was slimmed to these seven modules on 23 Nov 2026; see [the design](docs/superpowers/specs/2026-11-23-slim-roadmap-m6-m12-design.md) for what was cut and why.

## Run it

You need Docker Desktop, Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Node 24 LTS for the frontend.

**Everything in containers** (Postgres, Mailpit, MinIO, fakepay, RabbitMQ, Redis, then a one-off `migrate` job, then the API, the Celery worker, beat and the outbox relay, and Prometheus, Grafana and Jaeger to watch them):

```bash
docker compose up --build
curl localhost:8000/api/v1/health
# {"status":"ok","checks":{"database":"ok"}}
```

**Day to day** (everything but the API in Docker, the API and the frontend on Windows, both reloading as you edit):

```bash
docker compose up -d db mailpit minio fakepay rabbitmq redis worker beat relay \
                     prometheus grafana jaeger
                                      # Postgres, a fake inbox at http://localhost:8025, S3 storage,
                                      # the pretend payment gateway at http://localhost:8090,
                                      # RabbitMQ (its console at http://localhost:15672, ticketloop /
                                      # ticketloop), Redis for the cache and the rate limits, the
                                      # worker, beat and relay that move tickets, and the dashboard
                                      # at http://localhost:3000 and traces at http://localhost:16686
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

Celery does not run on Windows, so the worker, beat and relay run in Docker even when the API does not. After changing code they use, rebuild them: `docker compose up -d --build worker beat relay`.

MinIO stores the uploaded proof of purchase. Its web console is at http://localhost:9001 (user `ticketloop`, password `ticketloop-local-secret`); the files sit in the bucket `ticketloop-documents`, which the API creates on startup when `ENVIRONMENT=local`. Anywhere else, create the bucket yourself and set `STORAGE_ENDPOINT_URL`, `STORAGE_PUBLIC_URL` (the host browsers reach, used to sign download links), `STORAGE_BUCKET`, `STORAGE_ACCESS_KEY`, `STORAGE_SECRET_KEY` and `STORAGE_REGION`; see `.env.example`. Staging and production refuse to start with the built-in storage secret.

Set `LOG_JSON=false` in `.env` for coloured, readable log lines while developing. Stop the `api` container first (`docker compose stop api`) if it is running, since both use port 8000.

```bash
docker compose down      # stop everything; data survives in the pgdata, miniodata, fakepaydata,
                         # rabbitmqdata and prometheusdata volumes
docker compose down -v   # stop and delete the databases and the stored files too
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

`ACTIVE` listings whose end has passed become `EXPIRED` when the expiry job runs, every 5 minutes in Docker. To run it by hand:

```bash
uv run python -m scripts.expire_listings   # Expired 0 listing(s).
```

## Try the search

http://localhost:5173 is the search page, with or without an account. To see it full, seed a separate database with 2,000 events and 100,000 listings (Faker; it refuses any database whose name does not end in `_perf`) and run the API against it:

```bash
export DATABASE_URL=postgresql+psycopg://ticketloop:ticketloop@localhost:5432/ticketloop_perf
uv run python -m scripts.seed_discovery --reset
uv run python -m scripts.explain_discovery   # every query's plan, page 1 against page 500; writes docs/discovery-performance.md
uv run uvicorn app.main:app
unset DATABASE_URL                           # back to the development database
```

(In PowerShell: `$env:DATABASE_URL = '…'`, and `Remove-Item Env:DATABASE_URL` after.)

```bash
curl -s "$B/listings?q=cup&city=Mumbai&max_price_paise=500000&sort=-created_at" | jq '.items[].event.name'
```

## Try the reservation

Open a listing from the search in two browsers with two accounts (neither the seller's) and press **Reserve** in both. One gets a page counting down from 10:00; the other is told somebody was first. **Cancel hold** gives the ticket back; **My holds** lists every hold.

```bash
curl -s -X POST $B/listings/$LISTING/reservations -H "Authorization: Bearer $BUYER"   # 201, or 200 if you hold it
curl -s $B/reservations/me -H "Authorization: Bearer $BUYER" | jq '.items[0]'
```

Holds that run out are closed every 30 seconds by Celery beat in Docker, or by hand:

```bash
uv run python -m scripts.expire_reservations          # every 30 seconds; --once for a single pass
```

## Try paying for it

Carry on from a hold. **Checkout** takes the buyer to fakepay's own page, where you choose what the payment does:

- **Pay**: back on the order's page, it turns to **Paid** as soon as the webhook lands, then to **Completed** once the worker has handed the ticket over. It is under the buyer's **My tickets** now, and the seller's listing says **Sold**.
- **Pay, webhook late**: the order's page says it is waiting, and turns to Paid by itself.
- **Pay, webhook twice** and **Pay, forged webhook**: the first still pays once; the second is refused with a 401, and reconciliation finds the payment instead.
- **Pay, no webhook**: the money moved and nobody told us. Run reconciliation (below) five minutes later.
- **Decline**: the order fails and the ticket is back on sale.

Close the tab halfway and come back through **My orders**: it is the same payment, never a second one. From a terminal, `Idempotency-Key` is whatever you like, the same one on a retry:

```bash
HOLD=$(curl -s -X POST $B/listings/$LISTING/reservations -H "Authorization: Bearer $BUYER" | jq -r .id)
ORDER=$(curl -s $B/orders -H "Authorization: Bearer $BUYER" -H 'Idempotency-Key: try-1' \
  -H 'content-type: application/json' -d "{\"reservation_id\":\"$HOLD\"}" | jq -r .id)
curl -s $B/payments/intents -H "Authorization: Bearer $BUYER" -H 'Idempotency-Key: try-2' \
  -H 'content-type: application/json' -d "{\"order_id\":\"$ORDER\"}" | jq -r .checkout_url   # open it, or:
# curl -s localhost:8090/v1/intents/<transaction_id>/outcome -H 'content-type: application/json' -d '{"outcome":"succeed"}'
curl -s $B/orders/$ORDER -H "Authorization: Bearer $BUYER" | jq -r .status                       # PAID
```

Payments nobody told us about are found by asking the gateway, every 2 minutes in Docker, or by hand:

```bash
uv run python -m scripts.reconcile_payments           # SUCCEEDED: checked 1, fixed 1.
```

`scripts.expire_reservations` asks too, about buyers who went to pay and never came back, and never releases a ticket without an answer. Give the API and fakepay the same `FAKEPAY_WEBHOOK_SECRET` (32 bytes or more) anywhere but your laptop; staging and production refuse to start with the built-in one.

The races themselves, against four API processes and a database of their own:

```bash
uv run pytest tests/concurrency                                              # every race, 3 rounds
RACE_ROUNDS=20 uv run pytest tests/concurrency/test_reservation_race.py      # the recorded proof
uv run pytest tests/concurrency/test_payment_race.py                         # orders, webhooks, the clock
uv run python -m tests.concurrency.report                                    # three locking strategies, compared
uv run pytest tests/concurrency/test_messaging.py                            # the M9 drills; needs RabbitMQ
```

## Watch the ticket change hands

With the worker, beat and relay running, every paid order goes on by itself: the relay publishes `payment.succeeded`, a worker creates the transfer, another asks the pretend ticket provider, and the ticket becomes the buyer's. If a message is lost on the way (it ended in a dead-letter queue), beat's `resume_transfers` job picks the order up again after 10 minutes. The worker, beat and relay restart by themselves if they exit. RabbitMQ's console (http://localhost:15672) shows the queues `transfers`, `notifications` and `maintenance`, and their dead-letter queues.

```bash
curl -s $B/orders/$ORDER -H "Authorization: Bearer $BUYER" | jq '{status, transfer: .transfer.status}'
curl -s $B/notifications -H "Authorization: Bearer $BUYER" | jq '.items[].notification_type'
docker compose logs -f worker relay                                      # what they did, as JSON
```

The drills, by hand:

- **The broker is down:** `docker compose stop rabbitmq`, pay for a few orders, and `outbox_events` fills up with unpublished rows. `docker compose start rabbitmq`: the relay publishes them all, and each ticket moves once.
- **A worker dies mid-transfer:** set `TICKET_PROVIDER_DELAY_SECONDS: "30"` on the worker in `docker-compose.yml`, pay, `docker compose kill worker` while it waits, then `docker compose up -d worker`. The message comes back, the provider is asked again with the same key, and the ticket moves once.
- **The provider fails:** `TICKET_PROVIDER_MODE` set to `retryable` (with `TICKET_PROVIDER_FAILURES`), `permanent` or `unknown`. Busy is retried 5 times, then the transfer and the order go to review; the other two go there at once.

(In PowerShell: `$env:RACE_ROUNDS = '20'` first, and `Remove-Item Env:RACE_ROUNDS` after.)

## Watch the cache and the limits

Search (`GET /listings` and `GET /events/{id}/listings`, first page) and the event page (`GET /events/{id}`) are cached in Redis for 30 to 60 seconds. Every answer says how it was served in `X-Cache`: `MISS` (computed, then stored), `HIT` (from Redis), or `BYPASS` (not cached: a later page, caching off, or Redis down). Any committed change to a listing or an event, from the API or the worker, makes the cached pages that depend on it unreachable at once (a change made by one of the hand-run scripts in `scripts/` shows within a minute), so a reservation, a review or an edit is never hidden behind a stale page.

```bash
curl -si "$B/listings?q=coldplay" | grep -i x-cache     # MISS
curl -si "$B/listings?q=coldplay" | grep -i x-cache     # HIT
docker compose exec redis redis-cli --scan --pattern 'cache:*'   # the keys and the versions
```

The rate limits are token buckets in Redis. Over the limit, the API answers `429` with `Retry-After` and the problem type `rate-limited`, and the form shows a countdown.

| Route | Limit (a minute) | Counted per | Setting |
| --- | --- | --- | --- |
| `POST /auth/login`, `POST /auth/password-reset/request` | 5 | email and address, each route its own bucket | `RATE_LIMIT_LOGIN_PER_MINUTE` |
| `POST /listings/{id}/reservations` | 10 | user | `RATE_LIMIT_RESERVE_PER_MINUTE` |
| `POST /payments/intents` | 10 | user | `RATE_LIMIT_PAYMENT_PER_MINUTE` |
| `GET /listings`, `GET /events/{id}/listings` | 60 | address | `RATE_LIMIT_SEARCH_PER_MINUTE` |

```bash
for i in 1 2 3 4 5 6; do curl -s -o /dev/null -w '%{http_code} ' $B/auth/login \
  -H 'content-type: application/json' -d '{"email":"a@b.co","password":"wrong"}'; done
# 401 401 401 401 401 429
```

**Redis is down:** `docker compose stop redis`. Every route still answers, from Postgres, with `X-Cache: BYPASS` and no limits; the API logs `redis.unavailable` once, stops asking Redis, and pings it in the background every 5 seconds until it answers (`redis.available`). Only the request that found it gone waited: in Docker, about 4 seconds, while the stopped container's name fails to resolve. `docker compose start redis` and caching and limits come back by themselves. Redis never decides anything that matters: who holds a ticket, what was paid and who owns it are all Postgres.

`CACHE_ENABLED=false` or `RATE_LIMIT_ENABLED=false` turns either off; `REDIS_URL` says where Redis is.

## Watch it run: metrics, traces and logs

| What | Where |
| --- | --- |
| Grafana: one dashboard with requests, errors and latency per route, reservations, payments, the outbox, the queues, the worker, the cache and the limits | http://localhost:3000 (no login to look; `admin` / `ticketloop` to edit) |
| Prometheus: every series, and whether each target is up | http://localhost:9090 (Status, then Target health) |
| Jaeger: traces | http://localhost:16686 |
| The raw numbers | http://localhost:8000/metrics for the API; the worker's (:9100) and the relay's (:9101) inside the compose network |

Every label takes its value from a short fixed list: a route's template (`/api/v1/listings/{listing_id}`), never the path asked for; a problem code from the known ones, else `other`; a webhook's event type, else `unknown`. Made-up paths and forged webhooks add no new series. Prometheus scrapes the API on the host's port 8000, so it finds it whether it runs in Docker or on Windows.

Tracing is on in Compose (`TRACING_ENABLED=true`, `OTLP_ENDPOINT=http://jaeger:4318`). For an API run on Windows, put `TRACING_ENABLED=true` in `.env`; it sends to Jaeger's published port on localhost. With Jaeger stopped nothing waits: spans are sent in batches from a background thread, and dropped.

**From a failed purchase to its failing step:**

```bash
TICKET_PROVIDER_MODE=permanent docker compose up -d worker   # the provider refuses every transfer
# buy a ticket as in "Try paying for it"; the order ends TRANSFER_FAILED
docker compose logs --no-log-prefix worker | grep $ORDER | jq -c '{event, reason, correlation_id}'
# {"event":"transfer.needs_review","reason":"refused: the provider refused: ticket not transferable",
#  "correlation_id":"08403b20745a7b0161d8edac5d403d85"}
# open http://localhost:16686/trace/08403b20745a7b0161d8edac5d403d85: the webhook, the relay's
# publishes and the worker's tasks in one trace, and the span "ticket provider transfer" marked
# as an error, with the exception on it
docker compose up -d worker                                  # back to succeeding
```

(In PowerShell: `$env:TICKET_PROVIDER_MODE = 'permanent'` before the first line, and `Remove-Item Env:TICKET_PROVIDER_MODE` before the last.) Jaeger finds the same trace by the order too: Search, service `ticketloop-worker`, tags `ticketloop.order_id=<the order id>`.

`/metrics` is outside `/api/v1` and needs no token, like the health check. Deployed, it belongs behind the network edge, where Prometheus can reach it and the public cannot.

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
REQUIRE_BROKER=1 uv run pytest     # and for the RabbitMQ drills (docker compose up -d rabbitmq)
REQUIRE_REDIS=1 uv run pytest      # and for the cache and rate-limit tests (docker compose up -d redis)
uv run ruff check .
uv run ruff format .
uvx bandit==1.9.4 -r app fakepay --severity-level high    # CI's security job, the Python half
```

The frontend, from `frontend/`:

```bash
npm test                  # unit tests (Vitest)
npm run e2e               # browser tests in your Chrome; needs `docker compose up -d db mailpit minio rabbitmq redis`.
                          # They start their own API (8001) with a worker and relay beside it,
                          # fakepay (8091) and dev server (5174), on their own database,
                          # ticketloop_e2e, their own queues (prefix e2e-) and Redis database 1
npm run typecheck && npm run lint && npm run format:check
npm run api:types         # after changing the API: regenerate openapi.json and the TypeScript types
npm audit --audit-level=high
```

## Layout

```text
app/
  main.py                  app factory; uvicorn runs app.main:app
  api/                     /api/v1 router and the health check
  core/                    config, logging, request-ID middleware, error format, security (hashing, JWT),
                           metrics (/metrics) and tracing
  models.py                imports every model, so Alembic sees every table
  modules/                 one package per business module: models, schemas, service, router
    identity/              users, tokens, auth routes, get_current_user and require_admin
    events/                events, ticket providers, the event status machine, admin event routes
    tickets/               ticket registration, evidence uploads and links, admin verification
    listings/              listings, the listing state machine, review, history, the event cascade and expiry
    discovery/             public search: filters, sorts, the event's listings
    reservations/          10-minute holds: the race, cancelling, expiry
    orders/                orders: one per held ticket, at the listing's price
    payments/              payment intents, signed webhooks, settling, reconciliation
    outbox/                events written with their change, processed_events, and the relay
    transfers/             the transfer state machine and its two consumers: start, then perform
    notifications/         in-app notifications, written by a consumer, read through two routes
    idempotency/           Idempotency-Key: a request sent twice does its work once
    audit/                 the append-only audit log, record() to write to it, and the state machine class
  infrastructure/          Postgres engine, sessions, base model, email sender, object storage (S3 and in-memory), the payment gateway's client, the pretend ticket provider, Redis (the cache, the session hooks that invalidate it, the rate limiter)
  workers/                 Celery: the app and its queues, the tasks around the consumers, the scheduled jobs, the relay's loop
fakepay/                   the pretend payment gateway: its own app, database and checkout page
observability/             Prometheus's scrape config, Grafana's data sources and the dashboard, mounted by compose
tests/
  unit/                    fast, no external services
  integration/             need Postgres
  concurrency/             real races: four API processes, a real fakepay, 100 buyers; report.py compares strategies;
                           the messaging drills against RabbitMQ and real Celery workers
migrations/                Alembic env.py and one file per schema change
scripts/                   experiments and operator tools: lock_experiment, make_admin, export_openapi,
                           expire_listings, expire_reservations, reconcile_payments, seed_discovery, explain_discovery, databases (throwaway ones), e2e_api
frontend/                  React + TypeScript app (Vite); each module adds its screens
  src/api/                 typed client; schema.d.ts is generated from the backend
  src/auth/                access token in memory, refresh through the HttpOnly cookie
  src/pages/               one file per screen
  e2e/                     Playwright browser tests against the real stack
.github/workflows/ci.yml   backend tests, frontend checks, browser tests, the image started with production
                           settings, and Bandit, pip-audit and npm audit, on every push
docs/                      design documents (below)
documentation/             one HTML page per finished module for revision and interview prep; open index.html
docker-compose.yml         Postgres, Mailpit, MinIO, fakepay, RabbitMQ, Redis, the migrate job, the API, the worker, beat, the relay,
                           Prometheus, Grafana, Jaeger
Dockerfile                 the API image, which also runs fakepay, the worker, beat and the relay
```

## Design docs

| Document | What it answers |
| --- | --- |
| [`docs/requirements.md`](docs/requirements.md) | Every FR and NFR as checkable rules, with the test that proves each |
| [`docs/database-design.md`](docs/database-design.md) | ER diagrams, which constraint enforces which rule, indexes, changes from `plan.md` |
| [`docs/schema-draft.sql`](docs/schema-draft.sql) | The full target DDL; loads cleanly into Postgres 17 |
| [`docs/schema-checks.sql`](docs/schema-checks.sql) | Statements that should be rejected by the schema's constraints |
| [`docs/state-machines.md`](docs/state-machines.md) | Listing, reservation, order, payment and transfer lifecycles |
| [`docs/api-contract.md`](docs/api-contract.md) | Every endpoint: who may call it, request, response, errors |
| [`docs/hard-problems.md`](docs/hard-problems.md) | The six hard problems as exact SQL, with crash analysis |
| [`docs/reservation-design.md`](docs/reservation-design.md) | How one of 100 buyers wins, and what three locking strategies cost (measured) |
| [`docs/discovery-performance.md`](docs/discovery-performance.md) | Search query plans and timings on 100,000 listings (generated) |
| [`docs/purchase-flow.md`](docs/purchase-flow.md) | The end-to-end purchase and what every table holds at each step |
