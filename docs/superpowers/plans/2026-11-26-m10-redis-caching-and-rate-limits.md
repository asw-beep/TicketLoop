# M10 Redis: caching and rate limits Implementation Plan

> Executed inline, test first, one fresh review at the end, one commit `M10: Redis caching and rate limits`.

**Goal:** the hot public reads come from Redis when they can, never stale after a change commits, and the endpoints worth abusing refuse politely with 429; with Redis stopped, every answer is the same, only slower.

**Architecture:** `app/infrastructure/redis` holds the client, a small circuit breaker, the cache (versions, get, set) and the token-bucket limiter (one Lua script). Invalidation is not called by services: SQLAlchemy session hooks notice any write to `listings` or `events` and, after the commit, bump a version that every cache key contains. Routes read through `cached_response()`, which answers raw JSON with an `X-Cache` header. Limits are FastAPI dependencies that raise a `rate-limited` problem with `Retry-After`. The frontend shows one shared message with a countdown wherever a form shows its error.

**Tech Stack:** `redis==8.2.0` (redis-py), `redis:8.4.0-alpine`; nothing new on the frontend.

**Spec:** `docs/superpowers/specs/2026-11-23-slim-roadmap-m6-m12-design.md`, M10; `docs/api-contract.md` (429 rows); `docs/hard-problems.md`.

## Global Constraints

- Redis is never consulted in a reservation, order, payment or transfer decision. A stale cached search can only send a buyer to a listing; Postgres decides.
- Redis down: the cache falls back to Postgres and the limiter allows, logged and counted.
- Every limit is a setting, and caching and limiting can each be switched off (`CACHE_ENABLED`, `RATE_LIMIT_ENABLED`).
- Versions are bumped after the commit, never before; a reader reads the version before it queries Postgres.
- Cache TTLs jittered between 30 and 60 seconds.
- Starting limits: login 5 a minute (email plus IP), reserve 10 (user), payment intents 10 (user), search 60 (IP).
- Pinned: `redis==8.2.0`, `redis:8.4.0-alpine`.

## Review Focus

1. A write committing while a reader fills the cache: the reader's entry is never served after the commit.
2. Two searches that differ only in a filter, the sort or the limit: never the same cached page.
3. Redis down, slow or coming and going: correct answers without waiting on Redis each time, and the limiter allows.
4. Login keyed on email plus IP: the same email in other casing or with spaces shares the bucket; the 429 says nothing about whether the account exists.
5. A rolled-back write, or a write in a worker (transfer completing, expiry): the cache follows the database, not the request.

## Rulings made in advance

- **Two versions, `listings` and `events`, found by the session.** A hook on `do_orm_execute` and `after_flush` sees every insert, update or delete on the `listings` and `events` tables, including the conditional `UPDATE`s of the state machines and the bulk ones, and marks the session; `after_commit` bumps; `after_rollback` forgets. A write to `events` bumps both (an event's status decides which listings search shows). The spec's per-event version becomes one events version: a bulk `UPDATE` does not say which event it touched without reading its WHERE clause, and events change by an admin a few times a day.
- **The session finds its cache in `session.info`.** `get_db` puts the app's cache there; the worker runtime's session factory carries it in `info`. A session with no cache bumps nothing.
- **Measured by `X-Cache: HIT | MISS | BYPASS`** on cached routes, and by in-process counters (`hits`, `misses`, `errors`, limiter `allowed`, `limited`, `errors`) that M11 exports to Prometheus.
- **A circuit breaker:** after a Redis error, neither cache nor limiter tries Redis for 5 seconds. Without it every request would wait for a connect timeout.
- **Client IP from the socket**, never `X-Forwarded-For`, which anyone can send.
- **Password-reset requests share the login limit's shape** (5 a minute, email plus IP): the contract already lists 429 there.
- **Tests:** caching and limiting are off in the test settings; the M10 tests turn them on against Redis database 15, flushed before each test, skipped without Redis unless `REQUIRE_REDIS=1` (CI). The browser tests run with the cache on (Redis database 1, flushed at the start) and the limits raised out of the way; the rate-limit screen is tested with a 429 the test serves itself.

## Tasks

### 1. Redis in the stack

Settings (`redis_url`, `redis_timeout_seconds` 0.25, `cache_enabled`, `cache_ttl_min_seconds` 30, `cache_ttl_max_seconds` 60, `rate_limit_enabled`, one `rate_limit_<name>_per_minute` per limit), the client, `redis` in Compose (with a health check) and CI service containers, `REQUIRE_REDIS=1` in the test job, a `redis` fixture. Test: the settings' defaults; the client is built with short timeouts.

### 2. The cache

`Breaker`, `Cache.versions(*names)`, `get`, `set` (jittered TTL), `bump(*names)`; every Redis error is caught, logged once per breaker trip and counted. Session hooks in `app/infrastructure/redis/invalidation.py`. Tests: a set value comes back; TTL within 30 to 60; Redis unreachable gives `None` at once the second time (breaker); a listing status change through the state machine, a bulk listing update and an ORM insert each bump `listings` after commit and not before; a rolled-back change bumps nothing; an event change bumps both.

### 3. Cached routes

`cached_response(request, versions, key_parts, compute)` for `GET /events/{event_id}`, and the first page (no cursor) of `GET /listings` and `GET /events/{event_id}/listings`. Tests: the second call is a HIT with the same body; any filter, sort or limit change is a MISS; approving, reserving and cancelling a listing each make the next search a MISS that shows the change; a reservation attempted from a stale cached search still gets its 409; with Redis stopped every answer is the same, with `X-Cache: BYPASS`.

### 4. Rate limits

`TokenBucket` (Lua: refill by Redis's own clock, take one, answer allowed and the wait), `RateLimitedError` (429 `rate-limited`, `Retry-After` in whole seconds, at least 1), dependencies for reserve, payment intents and both searches, calls inside login and password-reset request. Tests: the sixth login in a minute is 429 with `Retry-After`; the bucket refills; a different email or IP has its own bucket; `"A@x.com "` shares `"a@x.com"`'s; the 429 is the same for an account that does not exist; Redis down allows; limiting off allows.

### 5. The rate-limit message

`ApiError.retryAfter` from the response header; `FormError` shows, for `rate-limited`, "Too many tries. Try again in N seconds." counting down to "You can try again now." Unit tests for reading the header and the countdown; a browser test that serves a 429 to the login form and watches the countdown.

### 6. Docs and ship

README (Redis in the commands, the settings, how to watch the hit ratio), api-contract (429s as built, `X-Cache`), hard-problems (the cache and the limiter are not in any decision), the documentation page with the fail-open trade-off written up, commit, push, CI.
