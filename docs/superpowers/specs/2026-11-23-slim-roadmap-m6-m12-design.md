# Slim roadmap, M6 to M12: design

Date: 2026-11-23. Replaces old M6 to M18 in `build-and-learn-plan.md`. Sources: `build-and-learn-plan.md` (old M6, M8 to M12, M14 to M17), `docs/requirements.md`, `docs/state-machines.md`, `docs/database-design.md`, `docs/api-contract.md`, `docs/hard-problems.md`, the M5 design.

## Intent

TicketLoop is a resume project. It will not launch. What is left of it should go where an interviewer looks: correctness under concurrency, idempotency, payment webhooks, reliable messaging, caching, observability, and numbers measured under load. Product scope that only a real launch needs is cut.

Thirteen modules (about 79 plan days) remained after M5, and one week is available. This design merges and trims them into seven modules, one per day, finishing on Monday 30 November 2026, and settles in advance the decisions each module's plan would otherwise stop to ask about.

## Goals

1. A buyer finds an active listing, reserves it, pays through a fake gateway, and ends up owning the ticket, in the browser and through the API.
2. Each module leaves one resume claim backed by a test or script anyone can rerun:

| Module | Claim | Proof |
| --- | --- | --- |
| M6 · Discovery | Keyset-paginated search over 100,000 listings; every common filter served by an index; page 500 as fast as page 1 | `EXPLAIN ANALYZE` output and timings, recorded |
| M7 · Reservations | 100 concurrent buyers, one listing, exactly one reservation, 20 runs in a row on 4 API workers; three locking strategies compared | The race harness and `docs/reservation-design.md` |
| M8 · Orders and payments | 50 concurrent requests with one `Idempotency-Key` make one order; HMAC-verified webhooks applied once in effect; reconciliation repairs drift; expiry never releases a paid listing | Concurrency and webhook tests |
| M9 · Outbox and transfer | Events survive a broker outage and worker crashes: nothing lost, nothing applied twice; a paid order completes by moving the ticket to the buyer | Happy-path integration test and the recorded drills |
| M10 · Redis | Cache-aside with invalidation after commit; atomic token-bucket rate limits; correct answers with Redis down; no inventory decision reads Redis | Cache, rate-limit and Redis-down tests |
| M11 · Observability and delivery | A Grafana dashboard of the golden signals and business metrics; one trace from API through RabbitMQ to the worker; a scanned Docker image built in CI | The dashboard, a trace, CI |
| M12 · Load testing | Measured throughput and p50, p95, p99; 1,000 users on one listing give one reservation and no 5xx; zero oversells in every run | k6 scripts, `check_invariants.sql`, `docs/test-results.md` |

3. Every module still ends as one commit `Mn: Title` with its documentation page, pushed to `main`, CI green.
4. M12 writes the measured numbers, with the hardware, data volume and script behind each, into the README.

## Non-goals (cut)

- **Interests and allocation** (old M7, FR-06). Buyers reserve directly. First come, first served is the race itself.
- **Disputes, account suspension and the transaction-history timeline** (old M13, the dispute half of FR-12). The route-enumerating admin guard test already exists (M5).
- **Refunds of every kind.** A transfer that fails for good stops in `REQUIRES_MANUAL_REVIEW` with the order `TRANSFER_FAILED`; there are no admin retry or abandon actions. Cancelling an event after a sale does nothing to its orders. No `refunds` table.
- **A fake ticket-provider service.** The transfer calls an in-process adapter. A second fake service would add a day and no new idea; `fakepay` already shows webhooks crossing a process boundary.
- **Frontend polish** (old M18): design pass, phone layouts, accessibility audit. Notification screens.
- From old M15: alert rules. From old M16: the ZAP scan, a Content Security Policy, pushing the image to a registry.
- From old M17: stress testing toward 10,000 users, soak, deadlock and Hermitage isolation suites, Hypothesis, Testcontainers, Toxiproxy, connection-pool tuning.
- Changing anything M0 to M5 built, admin listing review included, except where a module below says so (the shared pagination helper, the home page, the event-cancel guard).

## Anti-goals (a failure even if everything "works")

- A resume claim with no rerunnable test or script behind it, or a number reported without its hardware, data volume and script.
- Redis consulted in a reservation, order, payment or transfer decision.
- A status change that skips its transition function or its audit row (the M5 pattern applies to reservations, orders, payments and transfers).
- A webhook, outbox event or consumer message applied twice in effect.
- Saving time by cutting the tests of what ships. When a day runs short, scope inside the module shrinks, starting with its "first to drop" line, never the tests of what remains.

## Constraints

- The stack stays: FastAPI, sync SQLAlchemy 2, Postgres 17, Alembic, uv, React 19 with TanStack Query, Playwright. New: Faker, Redis, RabbitMQ, Celery, Prometheus, Grafana, Jaeger, k6 (from its Docker image). Versions pinned exactly, as so far.
- The development machine is Windows. Celery does not support Windows, so the worker, beat, outbox relay, `fakepay`, RabbitMQ, Redis, Prometheus, Grafana, Jaeger and k6 run in Docker Compose. Day to day the API still runs on the host with uv; the compose `api` service runs it in a container for multi-worker races and load tests. Frontend tools run from the uppercase `E:\` path.
- CI adds Redis and RabbitMQ service containers in the module that first needs each.
- Money is paise in `bigint`, INR only.
- Everything M0 to M5 established still holds: problem+json errors, services that never commit, one 404 for "missing" and "not yours", audit rows only through `record()`, transitions through `ALLOWED` plus a conditional `UPDATE`.
- `docs/api-contract.md` fixes the routes, payloads and status codes for reservations, orders, payments and notifications. A plan follows it, and records any deviation as a ruling and updates the contract.
- No AI attribution in commits, pull requests or files.

## Approaches

1. **One design for all seven modules now, then build straight through. Chosen.** The decisions are made once, today; each day goes plan, build, review, ship, with no design conversation in between. Cost: later modules are decided before earlier ones are seen built, so a change of mind mid-week means editing this document.
2. **A design conversation per module**, as M3 to M5 had. Rejected: a round of questions per module does not fit one week, and load testing, the source of the resume numbers, would be the casualty.

Within the modules, three calls worth naming:

- **`fakepay` stays a separate service**, because a webhook arriving from another process, signed and possibly duplicated, is the point of M8. The ticket provider becomes an in-process adapter, because nothing new is learned from a second fake service.
- **Notifications stay as a backend-only second consumer**, because two consumers of one event, each applying it once, shows per-consumer deduplication in `processed_events`. They are the first thing M9 drops if it runs long.
- **The locking comparison stays**, timeboxed: the production path is the conditional `UPDATE`; the two alternatives exist only in the test harness. "I measured three strategies and here is why I chose this one" is the best interview answer in the project.

## How the week runs

- **Per module:** a plan (writing-plans) derived from this design and the existing docs, then inline execution test-first (executing-plans), one fresh whole-branch review on the most capable model, one fix pass, the documentation page `documentation/mNN-*.html` and its index entry, the README module table, then one commit `Mn: Title`, pushed, with CI watched until green.
- **No stops for approval** between modules: no design conversation, no plan approval. The owner is asked only when a decision is genuinely theirs, or when something destructive or outward-facing comes up beyond the module's own push. Every decision a plan or its execution makes goes in that module's ruling ledger and its final message, as in M5.
- **Schedule:** Tue 24 M6 · Wed 25 M7 · Thu 26 M8 · Fri 27 M9 · Sat 28 M10 · Sun 29 M11 · Mon 30 M12. If a module overruns, the next module starts anyway on its own day; M12 shrinks first (to its "keep at minimum" line), then M11's tracing through RabbitMQ.
- **Screens only for the buying flow.** This replaces the standing rule that every module ships screens. M6 to M9 add the search, reserve, checkout and ownership screens; M10 adds one shared rate-limit message; M11 and M12 add none.
- **Browser tests:** one Playwright test grows, module by module, into the full purchase (search, reserve, pay, own the ticket). From M6, the browser tests run against their own database, recreated and migrated at the start of every run, which removes the local flakiness found in M5 (leftover rows filling the first page of admin lists).

**Before M6 starts**, one commit `Plan: slim the roadmap to seven modules` carries this design and brings the project docs in line with it:

- `build-and-learn-plan.md`: the new M6 to M12 replace old M6 to M18; the cut modules are listed under "Cut from the plan" with one line each on why.
- `docs/requirements.md`: FR-06 marked cut; FR-07 to FR-12 keep their numbers, point at the new modules, and lose interests, refunds, disputes and admin transfer actions.
- `docs/state-machines.md`: the interest machine removed; transitions involving interests, refunds, disputes and admin transfer retry or abandon removed; the order machine without refund states.
- `docs/database-design.md` and `docs/schema-draft.sql`: `interests`, `refunds`, `disputes`, `dispute_events` and `dispute_evidence` removed; `reservations.interest_id` dropped.
- `docs/api-contract.md`: the interest, dispute, admin-transfer and refund routes removed.
- `README.md` and `documentation/index.html`: the module table and roadmap show the new seven.

That commit is pushed together with M6's.

## The modules

### M6 · Discovery (Tue 24 Nov)

**Build**

- `scripts/seed_discovery.py` (Faker): 2,000 published events and 100,000 listings (with their verified tickets and sellers), most `ACTIVE`, the rest spread over the other statuses. It writes to the database named in `DATABASE_URL` and refuses unless that database's name ends in `_perf`, so it can never flood the development database.
- Public, no login: `GET /listings`, `GET /events`, `GET /events/{event_id}` and `GET /events/{event_id}/listings`, with the filters and sorts `api-contract.md` lists (event name, category, city, ticket category, price range; sort by price or listing date, both directions). Public responses use the contract's `PublicListing` schema: never a seller's email or name, ticket documents or provider references (FR-05). The existing `GET /listings/{listing_id}` keeps its M5 shape.
- Keyset pagination replaces the offset inside the shared `paginate()` helper, for every list endpoint, behind the same opaque cursor, as its docstring promised in M4. The cursor carries the sort name, the last row's sort value and its id; a cursor from a different sort is a 400 `bad-cursor`.
- Indexes from `database-design.md` (`ix_listings_active_by_price`, `ix_listings_active_by_created`, `ix_events_name_trgm` with `pg_trgm`, `ix_events_status_starts`), plus whatever `EXPLAIN ANALYZE` shows the joined filters need. Every index names its query; any the plans never use are dropped. Before-and-after plans and timings go in the module's documentation page.
- **Screens:** `/` becomes the search page for everyone (today it redirects to login or account): filters, sort, results with **Load more** over the cursor; an event page listing its tickets for sale. The existing listing page is the detail view.
- **Browser tests:** move to their own database, as above.

**Done when:** at 100,000 listings, `EXPLAIN ANALYZE` shows no sequential scan on `listings` for no filter, each single filter, a name search and each sort; and page 500's query time is within 20% of page 1's.

**First to drop:** the `GET /events` filters beyond name and city.

### M7 · Reservations (Wed 25 Nov)

**Build**

- `reservations` table from the schema draft, without `interest_id`: the partial unique index of one live reservation per listing (`CREATED`, `PAYMENT_PENDING`, `CONFIRMED`), a status CHECK, and `ix_reservations_due`.
- `POST /listings/{listing_id}/reservations`, one transaction: the listing's transition `ACTIVE → RESERVED` as a conditional `UPDATE ... WHERE status = 'ACTIVE' AND seller_id <> :buyer AND expires_at > now()`, then the reservation insert with `expires_at = now() + 10 minutes`, then the audit rows. No row updated means 409; a unique violation (the safety net) is the same 409. Repeating the request as the buyer who already holds it returns 200 with that reservation, as the contract says.
- `GET /reservations/{reservation_id}`, `POST /reservations/{reservation_id}/cancel` (only from `CREATED`; the listing returns to `ACTIVE`, or `EXPIRED` if its own end passed while held), and `GET /reservations/me` (live ones first, keyset) so the buyer can find a hold again.
- `scripts/expire_reservations.py`: a loop every 30 seconds that claims due `CREATED` reservations with `FOR UPDATE SKIP LOCKED`, expires each, and releases its listing in the same transaction. It never touches `PAYMENT_PENDING` (M8 resolves those). It moves to Celery beat in M9.
- **Event cancel:** refused with 409 `event-has-reservations` while any of its listings is `RESERVED`. A hold ends within 10 minutes unless a payment is under way, and a payment must not be cancelled by the clock. `SOLD` listings are left alone (no refunds).
- **The locking comparison:** two alternative reserve implementations, `SELECT ... FOR UPDATE` then a check in Python, and SERIALIZABLE with a retry on SQLSTATE 40001, live under `tests/concurrency/`, never in `app/`. The same harness runs all three; success counts, retries and p50 and p95 latency go in `docs/reservation-design.md` with the hardware.
- **The race harness:** real HTTP against `uvicorn --workers 4`, 100 different users released together by a barrier. It asserts one 201, 99 conflicts, one live reservation row, the listing `RESERVED` and the expected audit rows, by reading the database. Two more races: a seller cancelling while buyers reserve, and two expiry workers running together (each reservation expired exactly once, counted by audit rows). CI runs the harness at 100 users a few times per push; the 20-run proof runs on demand and is recorded.
- **Screens:** **Reserve** on the listing page for a signed-in buyer who is not the seller; a reservation page with a countdown from `expires_at` and **Cancel**; plain messages when someone else got there first or the hold ran out.

**Done when:** the 100-buyer race passes 20 runs in a row on 4 workers, and the comparison is recorded.

**First to drop:** the SERIALIZABLE variant of the comparison.

### M8 · Orders and payments (Thu 26 Nov)

**Build**

- `orders` (`reservation_id` unique, amount copied from the listing on the server, INR) and `idempotency_keys` (`(user_id, key)`, request hash, stored status and body, `locked_at`, 24-hour expiry), with the flow from Brandur Leach's article: insert the key with `ON CONFLICT DO NOTHING`; a different body is 422; a stored response is replayed with `Idempotent-Replayed: true`; a key still in progress is 409.
- The order state machine, M5 pattern, trimmed to `CREATED`, `PAYMENT_PENDING`, `PAID`, `PAYMENT_FAILED`, `CANCELLED`, `TRANSFER_PENDING`, `COMPLETED` and `TRANSFER_FAILED`.
- Routes as the contract names them: `POST /orders`, `GET /orders/{order_id}`, `GET /orders/me`, `POST /payments/intents`, `POST /payments/webhooks/{provider}`, `GET /payments/{payment_id}`.
- **`fakepay`**, a small FastAPI app in its own folder and container: create an intent (the payment's id is the idempotency key, so a repeat returns the same intent); a checkout page where the tester picks the outcome (succeed, fail, delay, webhook twice, bad signature, no webhook), which then returns the browser to the order page; the same outcomes through an API call, for tests and the M12 load test; a lookup endpoint for reconciliation. It signs webhooks with HMAC-SHA256 over the timestamp and the raw body, in the contract's `X-Fakepay-Signature` and `X-Fakepay-Timestamp` headers, with a secret shared through the environment. No refunds.
- `payments` (saved and committed as `CREATED` before the gateway is called), `payment_events` (unique on provider and provider event id), `reconciliation_runs`.
- **The webhook:** read the raw body; verify with `hmac.compare_digest`; reject a timestamp older than 5 minutes with 401; insert the event with `ON CONFLICT DO NOTHING` and answer a duplicate 200 at once. For a new event, in one transaction: check amount and currency against the order (a mismatch changes nothing and logs an alert), then apply success (payment `SUCCEEDED`, order `PAID`, reservation `CONFIRMED`) or failure (payment `FAILED`, order `PAYMENT_FAILED`, reservation `FAILED`, listing released) through the transition functions.
- **Safe expiry:** the expiry scan still takes only `CREATED`. A `PAYMENT_PENDING` reservation 15 minutes past its end is resolved by asking `fakepay`: paid means apply success, unpaid means expire and release, no answer means leave it for the next run.
- **Reconciliation** as a script (beat runs it from M9): payments still `CREATED` or `PENDING` after N minutes are checked against `fakepay` and repaired through the same transition functions; each run writes a `reconciliation_runs` row, and a second run changes nothing.
- The listing stays `RESERVED` after payment; M9's transfer makes it `SOLD`. The M8 demo ends at "Paid".
- **Tests:** 50 concurrent `POST /orders` with one key give one order and 50 identical bodies; the same key with a different body gives 422; an expired or someone else's reservation is refused; a webhook delivered 10 times changes state once; a forged signature or stale timestamp gives 401 and changes nothing; a late webhook after reconciliation is a no-op; an amount mismatch changes nothing; expiry racing payment success, 50 runs, never releases a paid listing; a gateway timeout leaves an order reconciliation can recover.
- **Screens:** **Checkout** from the reservation page (one `Idempotency-Key` per click, reused on retry), then `fakepay`'s page with its outcome buttons, then the order page, which polls until the payment settles; **My orders**.

**Done when:** every test above passes, and delayed, duplicated and failed payments all end in a correct state.

**First to drop:** the delay and no-webhook buttons on `fakepay`'s page (they stay as switches the tests use).

### M9 · Outbox, worker and transfer (Fri 27 Nov)

**Build**

- Compose gains `rabbitmq` (with the management UI), `worker`, `beat` and `relay`.
- `outbox_events` and `emit(session, aggregate_type, aggregate_id, event_type, payload)`, which inserts in the caller's transaction. Emitted: `payment.succeeded`, `payment.failed`, `reservation.expired`, `transfer.completed`. The table has a `headers` JSONB column from the start, empty until M11 stores trace context in it.
- **The relay:** a loop that takes unpublished rows with `FOR UPDATE SKIP LOCKED LIMIT 100`, publishes each, and sets `published_at`; on failure it raises `retry_count` and backs off. It publishes through Celery (`send_task`, one task per consumer per event, routed to that consumer's queue, the event id in the task), with publisher confirms switched on, so consumers stay ordinary Celery tasks. Two relays may run at once.
- **Consumers** are Celery tasks with `acks_late`, retries with exponential backoff and jitter, and a dead-letter queue after the last retry. `processed_events (consumer, event_id)` is written in the same transaction as the consumer's effect, so a redelivered message does nothing.
- **Transfer** (consumes `payment.succeeded`): `transfers` with `order_id` unique; `INITIATED`, order `TRANSFER_PENDING`; the in-process provider adapter (success by default; retryable failure, permanent failure and unknown outcome as test switches). On success, one transaction: transfer `COMPLETED`, order `COMPLETED`, listing `RESERVED → SOLD`, the ticket's `current_owner_id` set to the buyer, `transfer.completed` emitted, audit rows. Retryable failures retry up to 5 times; exhausted, permanent or unknown outcomes go to `REQUIRES_MANUAL_REVIEW` with the order `TRANSFER_FAILED`, and stay there (no admin actions, by the non-goals). Its listing stays `RESERVED` and its reservation `CONFIRMED`, so that event can no longer be cancelled (M7's guard). That is accepted and written on the documentation page.
- **Notifications** (consumes every emitted event): one notification per affected user per event (`uq_notifications_event_user`); `GET /notifications` and `PATCH /notifications/{notification_id}/read` as the contract names them. No screen.
- **Celery beat** runs reservation expiry every 30 seconds, listing expiry every 5 minutes and reconciliation every 2 minutes. The scripts stay runnable by hand.
- **Drills**, automated where a test can do it and otherwise scripted and recorded on the documentation page: RabbitMQ stopped while payments are made (outbox rows wait, then all publish and each applies once); a worker killed mid-task (redelivered, ignored the second time); a poison message (ends in the dead-letter queue).
- **Tests:** a duplicate `payment.succeeded` creates one transfer; a redelivered message has no second effect; one integration test runs reserve, order, pay, webhook, outbox, consumer to `COMPLETED` with the ticket in the buyer's name; a permanent failure ends in manual review.
- **Screens:** the order page shows transfer progress until **Completed**; the buyer's **My tickets** shows the bought ticket; the seller's listing shows **Sold**.

**Done when:** the happy-path test passes and the three drills are recorded with nothing lost and nothing applied twice.

**First to drop:** the notifications consumer.

### M10 · Redis caching and rate limiting (Sat 28 Nov)

**Build**

- Redis in Compose and CI; a client in `app/infrastructure/redis`; a `CACHE_ENABLED` setting so the effect can be measured.
- **Cache-aside** for `GET /events/{event_id}` and the first page of `GET /listings` and `GET /events/{event_id}/listings`, keyed on every filter and the sort, with 30 to 60 second jittered TTLs (the stampede protection). Invalidation by version numbers in the keys, bumped after commit: a per-event version for event pages, one listings version for search. Any listing change makes the old entries unreachable.
- **Rate limits:** a token bucket in one Lua script (atomic), keyed on user or IP per endpoint, login on email plus IP. Starting limits: login 5 a minute, reserve 10, payment intents 10, search 60. A refusal is a 429 problem with `Retry-After`. Every limit is a setting, and limiting can be switched off, because the browser tests and M12's load test send everything from one IP.
- **Redis down:** the cache fails open to Postgres, and so does the limiter, logged and counted. The trade-off (availability over brute-force protection during an outage, with Argon2 still slowing guesses) is written up on the documentation page.
- **Tests:** a second call is a hit; a listing change invalidates; a reservation attempted from a stale cached search still gets its 409 from Postgres; the sixth login in a minute gets 429 with `Retry-After`; with Redis stopped every answer is still correct.
- **Screens:** one shared rate-limit message with a countdown from `Retry-After`, used on login, search and reserve.

**Done when:** with Redis stopped the app gives the same answers, only slower, and the hit ratio is measurable.

**First to drop:** caching `GET /events/{event_id}/listings`.

### M11 · Observability and delivery (Sun 29 Nov)

**Build**

- Prometheus, Grafana and Jaeger in Compose; RabbitMQ's Prometheus plugin.
- `/metrics` through `prometheus-fastapi-instrumentator`, plus `reservation_attempts_total{result}`, `payment_events_total{type,outcome}`, a webhook-processing histogram, an outbox-backlog gauge, worker retries and checked-out database connections. Labels stay low-cardinality: never a user or listing id.
- A Grafana dashboard (request rate, errors and p50, p95, p99 per endpoint, plus the metrics above and queue depth), committed as JSON and provisioned on start.
- OpenTelemetry for FastAPI, SQLAlchemy, httpx and Celery. The trace context is stored with each outbox event, so the worker's span joins the API's trace; the trace id is bound to logs as `correlation_id`.
- **Delivery:** CI builds the existing non-root Docker image and checks that a container from it answers `/api/v1/health`; CI runs Bandit and pip-audit for the backend and `npm audit` for the frontend, failing on high severity.
- **Screens:** none; Grafana is this module's screen.

**Done when:** one failed purchase can be found in the logs by its order id, opened as a trace, and its failing step pointed at.

**First to drop:** the trace through RabbitMQ (API and database spans stay), then the Celery panels.

### M12 · Load testing and results (Mon 30 Nov)

**Build**

- `docs/test-plan.md`, written before anything runs: for each test, the hypothesis, the script, the metric and the pass line.
- k6 scripts with thresholds in `tests/load/`, against the Compose stack seeded by the M6 script:
  1. **Load:** stages of 100, 500 and 1,000 virtual users; 80% browse (search, view), 20% buy (reserve, order, pay with `fakepay` set to succeed). Target from NFR-03: reads under 300 ms p95, reservations under 500 ms p95, errors under 1%.
  2. **Spike:** 0 to 1,000 users in seconds on one listing. Exactly one reservation, every other buyer a clean 409, no 5xx.
  3. **Races at scale:** the M7 race 100 times on 4 workers, and M8's expiry-versus-payment race.
  4. **Broker drill under load:** RabbitMQ stopped mid-run; the outbox backlog grows on the dashboard, drains after restart, and every paid order completes once.
- The load runs raise the rate limits out of the way (one IP plays 1,000 users) and say so in the results; one short run with the real limits shows the 429s.
- `scripts/check_invariants.sql`, run after every test, returning zero rows: no ticket in two open listings, no listing with two live reservations, every succeeded payment's order `PAID` or later, every completed order's ticket owned by its buyer.
- `docs/test-results.md`: the tables, Grafana screenshots, hardware and data volume.
- **README rewrite:** a one-command start, an architecture diagram, a demo script (a full purchase and one failure drill in under 5 minutes) and a Results section with the numbers.
- **Screens:** none. The browser tests run once more against the finished stack.

**Done when:** each test has a script and a recorded result, pass or explained failure; the invariants returned zero rows after every run; the README's Results are filled from real runs.

**Keep at minimum:** the spike, the races at scale, the 100-user load stage, the invariants and the README Results.

## Data model, in summary

| Module | New tables |
| --- | --- |
| M7 | `reservations` |
| M8 | `orders`, `idempotency_keys`, `payments`, `payment_events`, `reconciliation_runs` |
| M9 | `outbox_events`, `processed_events`, `transfers`, `notifications` |

Never built: `interests`, `refunds`, `disputes`, `dispute_events`, `dispute_evidence`.

## Left to each module's plan

- Anything `api-contract.md` does not fix: problem codes, response fields beyond the contract, query parameter edge cases.
- Index choices beyond the named ones, decided by `EXPLAIN ANALYZE`.
- Seed volumes beyond the named minimums, k6 stage durations, histogram buckets, exact rate limits after a first measurement.
- Whether a drill is an automated test or a recorded script, when automating it would cost more than the day allows.
- File layout inside `app/modules/` and `frontend/src/`.

## Risks

- **No slack.** M8 and M9 are the heaviest days. The "first to drop" lines and M12's "keep at minimum" are the release valves, in that order.
- **Windows.** Multi-worker races and anything Celery run in containers or in CI on Linux; the plans must not assume the host can run them.
- **CI.** GitHub Actions service containers come only from published images, so in CI `fakepay`, the worker, beat and relay run as plain processes started by the job; only Postgres, Redis, RabbitMQ and Mailpit are service containers.
- **Webhook reachability.** `fakepay` in a container calls the API on the host at `host.docker.internal` in development, and by service name in Compose and CI.
- **Laptop numbers.** k6 results depend on the machine. They are reported with the hardware, as findings rather than promises.
