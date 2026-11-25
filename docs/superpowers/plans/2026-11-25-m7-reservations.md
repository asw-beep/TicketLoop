# M7 · Reservations and the race: implementation plan

**Goal:** a signed-in buyer holds an active listing for 10 minutes; of 100 buyers at once exactly one gets it, and a hold that runs out puts the ticket back on sale.

**Architecture:** the listing row stays the lock target. Reserving is the listing's `ACTIVE → RESERVED` transition with extra conditions in its UPDATE, then the reservation insert, in one transaction; a partial unique index is the safety net. The M5 transition code becomes one shared `StateMachine` used by listings and reservations (and M8's and M9's machines).

**Spec:** `docs/superpowers/specs/2026-11-23-slim-roadmap-m6-m12-design.md`, section M7. Also `docs/state-machines.md`, `docs/api-contract.md`, hard problem 1.

## Constraints

- Every status change goes through a machine: ALLOWED table, conditional UPDATE, audit row in the same transaction.
- Services never commit. One 404 for "missing" and "not yours". No AI attribution. One commit `M7: Reservations and the race`.
- Lock order is reservation before listing everywhere except reserving, where the reservation does not exist yet.

## Review focus

1. Event cancel racing a reserve: never a cancelled event with a reserved listing.
2. A double-clicked Reserve: one 201 and one 200 with the same reservation, never two holds or a 409 for your own hold.
3. A hold past `expires_at` that the expiry loop has not reached yet: not usable, and not reported as held.
4. The seller's history shows a reservation without saying who reserved.
5. A listing whose own end passed while it was held becomes `EXPIRED` on release, not `ACTIVE`.
6. Two expiry workers, or a worker and a cancel: one release, one audit row.
7. A buyer's clock that is wrong: the countdown still follows the server.

## Tasks

### 1. `StateMachine` (`app/modules/audit/machine.py`) and the reservation tables

- `StateMachine(model, entity_type, status_type, allowed, named, illegal_error, changed_error)` with `.allowed`, `.action` and `.move(db, row, to, *, actor_id, values=None, details=None, where=())`.
- `listings/state.py` keeps `ALLOWED`, `ACTION`, `transition(...)` as thin wrappers; its 68 tests pass unchanged.
- `reservations/models.py`: `Reservation`, `ReservationStatus`, `LIVE_STATUSES`; migration with the partial unique index, `ix_reservations_due`, `ix_reservations_buyer`, `ix_reservations_listing`.
- `reservations/state.py`: `ALLOWED`, `ACTION`, `transition()`. Test: all 36 pairs.

### 2. Reserving and cancelling

- `reservations/service.py`: `claim_listing(db, listing_id, buyer)`, `reserve(db, listing_id, buyer) -> (Reservation, created)`, `get_reservation_for`, `list_my_reservations`, `cancel_reservation`; `listings/service.release_listing(db, listing, actor_id, reason)`.
- Routes: `POST /listings/{id}/reservations` (201 or 200), `GET /reservations/me`, `GET /reservations/{id}`, `POST /reservations/{id}/cancel`.
- Errors: 403 `own-listing-reservation`, 409 `listing-not-available` (with `reason`: reserved, sold, expired, unavailable), 409 `reservation-not-cancellable`, 409 `reservation-changed`, 404 `reservation-not-found`.
- `Reservation` = contract fields plus `listing: {id, event, ticket_category, seat_details, asking_price_paise, original_price_paise}` (the buyer can no longer read `GET /listings/{id}` once it is `RESERVED`).
- History: `actor_role` gains `buyer`.
- Route guard tests extended (`test_listing_access.py`), new `test_reservation_access.py`.

### 3. Expiry and event cancel

- `expire_reservations(db, now=None, limit=100) -> int`: due `CREATED` rows, `FOR UPDATE SKIP LOCKED`, each expired and its listing released. `scripts/expire_reservations.py`: every 30 seconds, `--once` for one pass.
- `cancel_open_listings_for_event` locks `RESERVED` too and raises 409 `event-has-reservations` if it finds one.

### 4. The race harness

- `tests/concurrency/harness.py`: starts `uvicorn --workers 4` on a free port against a migrated `ticketloop_race_test`, makes 100 users with minted tokens, releases 100 threads from a barrier, returns statuses and latencies, then reads the database.
- `tests/concurrency/test_reservation_race.py`: `RACE_ROUNDS` (default 3), asserts 1 × 201, 99 × 409, one live row, listing `RESERVED`, audit rows; a seller cancelling against buyers; two expiry workers.
- `tests/concurrency/strategies.py` and `strategy_app.py`: `FOR UPDATE` and SERIALIZABLE variants.
- `scripts/race_report.py`: 20 rounds and the three strategies; writes `docs/reservation-design.md`.

### 5. Screens

- **Reserve** on the listing page; `/reservations/:id` with a countdown from the server's clock and **Cancel hold**; `/reservations` **My holds**; a seller's history line.
- Browser test: the purchase test starts here (search, reserve, hold, cancel).

### 6. Ship

- Docs, review, fix pass, commit, push, CI green.
