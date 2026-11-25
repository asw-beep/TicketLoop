# M6 · Discovery: implementation plan

**Goal:** anyone can search the tickets on sale, filter and sort them, and page through 100,000 listings with page 500 as fast as page 1.

**Architecture:** the shared `paginate()` becomes keyset. A new `discovery` module owns the public queries and their filters; `events` gains filters. One migration adds `pg_trgm` and the indexes. A seed script builds a `_perf` database, and a second script runs `EXPLAIN ANALYZE` on every query shape, fails on a sequential scan of `listings`, and writes `docs/discovery-performance.md`.

**Spec:** `docs/superpowers/specs/2026-11-23-slim-roadmap-m6-m12-design.md`, section M6. Routes: `docs/api-contract.md`.

## Constraints

- Public responses use `PublicListingOut`: no seller, documents or references.
- `limit` and `cursor` behave as before: 400 `bad-cursor`, 422 `limit`, default 20.
- Services never commit; no AI attribution; one commit `M6: Discovery`.

## Review focus

1. A NUL byte (`%00`), `%` or `_` in `q` or `city`: 422 for NUL, the other two match literally. Never a 500.
2. A blank `q` means no filter, not "no results".
3. `min_price_paise` above `max_price_paise` is 422, not an empty page that looks like "nothing on sale".
4. A cursor from another sort, or with tampered values (huge int, naive time, wrong type), is 400, never a 500.
5. An `ACTIVE` listing whose expiry has passed but that the expiry job has not reached yet is not shown: it cannot be reserved.
6. Rows with equal prices, or equal timestamps, are never repeated or skipped across pages.

## Tasks

### 1. Keyset `paginate()` (`app/core/pagination.py`)

- `Sort(name: str, keys: tuple[ColumnElement, ...], descending: bool = False)`, frozen; `keys` end in `id`.
- `paginate(db, stmt, sort, cursor, limit)` applies the order itself, filters with a row comparison (`(a, id) > (:a, :id)`), reads the key values back from the query.
- `encode_cursor(sort, values)` / `decode_cursor(cursor, sort)`: `{"s": name, "k": [...]}`. Values are parsed by each key's Python type (int inside bigint, aware datetime, UUID, str without NUL).
- All eight existing callers move to it. Tests: `tests/unit/test_pagination.py`, `tests/integration/test_pagination_db.py` (ties, both directions, a row inserted before the cursor, a cursor from another sort).

### 2. Filters and routes (`app/modules/discovery/`, `app/modules/events/`)

- `discovery/filters.py`: `ListingFilters` dataclass, `listing_filters()` dependency, `LISTING_SORTS`, `SearchText`.
- `discovery/service.py`: `search_listings(db, filters, sort, cursor, limit)`.
- `discovery/router.py`: `GET /listings`, `GET /events/{event_id}/listings`.
- `GET /events` gains `q`, `category`, `city`, `starts_after`, `starts_before`.
- New error: 422 `bad-price-range`.
- Tests: `tests/integration/test_discovery.py`, additions to `test_events.py`.

### 3. Indexes, seed, proof

- Migration: `CREATE EXTENSION pg_trgm`; `ix_listings_active_by_price`, `ix_listings_active_by_created`, `ix_events_name_trgm`, `ix_events_status_starts`, `ix_tickets_owner`, and what `EXPLAIN` asks for.
- `scripts/seed_discovery.py`: refuses unless the database name ends in `_perf`; `--reset`; COPY; deterministic; ends with `ANALYZE`.
- `scripts/explain_discovery.py`: every filter, the name search, every sort; page 1 against page 500, keyset against offset; exits 1 on a `Seq Scan on listings` or when page 500 is more than 20% slower; writes the report.

### 4. Screens and browser tests

- `SearchPage` at `/` (state in the URL), `EventPage` at `/events/:eventId`, nav link **Find tickets**.
- Playwright: its own API on 8001, its own Vite on 5174, database `ticketloop_e2e` recreated by `scripts/e2e_api.py`. One search test.

### 5. Ship

- Docs (contract, database design, README, documentation page and index), review, fix pass, commit, push, CI green.
