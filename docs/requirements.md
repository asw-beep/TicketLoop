# Requirements

Every functional (FR) and non-functional (NFR) requirement from `plan.md`, restated as rules we can check, with the test that proves each one and the module that builds it. Where `plan.md` was vague, the rule here is the decision we made; `database-design.md` and `state-machines.md` hold the details.

Conventions used everywhere:

- Money is an integer number of paise in a `BIGINT` column (`₹1,250.00` is `125000`). Currency is always `INR` for now.
- Timestamps are `TIMESTAMPTZ`, stored and returned in UTC.
- "Open" listing means DRAFT, PENDING_VERIFICATION, ACTIVE or RESERVED.
- A **seller** is not a role. Any USER who owns a verified ticket can sell it.

## Functional requirements

### FR-01 · Authentication and authorization (M3)

- Users register with email, password and full name. Emails are stored lowercased and are unique.
- Passwords are hashed with Argon2id. A plaintext password is never stored or logged, not even on a failed login.
- Login returns a 15-minute JWT access token and a 7-day refresh token.
- Refresh rotates: every refresh returns a new pair and revokes the old refresh token. Reusing a revoked refresh token revokes the whole token family (all sessions that descend from that login), because it means the token was stolen.
- Logout revokes the presented refresh token.
- Roles: `USER` and `ADMIN`. Admin routes live under `/admin` and require `ADMIN`.
- A `SUSPENDED` account cannot log in, refresh, or use an existing access token.
- Account recovery: a single-use, 30-minute reset token, stored hashed. The request endpoint answers the same way whether or not the email exists.
- Users read only their own private data. Asking for someone else's resource returns 404, not 403, so IDs cannot be probed.

**Test:** wrong password gives 401; expired and tampered tokens give 401; a reused refresh token kills the family; a suspended user gets 403; a USER calling any `/admin` route gets 403; user B reading user A's order gets 404.

### FR-02 · Event management (M4)

- An event has a name, description, category (`CONCERT`, `SPORTS`, `COLLEGE`, `OTHER`), venue, city, start time, organizer name, ticket provider and status (`DRAFT`, `PUBLISHED`, `CANCELLED`, `COMPLETED`).
- Only admins create and edit events. Everyone, including guests, can read `PUBLISHED` events.

**Test:** a USER creating an event gets 403; a guest sees published events and not drafts; list endpoints paginate.

### FR-03 · Ticket management (M4)

- A ticket belongs to one event and has an original owner and a current owner (different after a resale), a category, seat details, original price, purchase reference, provider reference, verification status (`PENDING`, `VERIFIED`, `REJECTED`), transfer eligibility and status (`ACTIVE`, `VOID`).
- The owner is taken from the access token, never from the request body.
- The same provider reference cannot be registered twice for one event, so one real ticket cannot be sold by two people.
- Purchase evidence is uploaded to a private bucket under a random key. Downloads use presigned URLs that expire after 5 minutes. QR codes, barcodes and documents are never in a public response.
- An uploaded screenshot proves nothing. An admin verifies tickets for now; a provider API replaces that later.

**Test:** a second registration of the same provider reference gives 409; user B gets 404 on user A's ticket and documents; an oversized or wrong-type upload is rejected; no public response contains `purchase_reference` or `provider_reference`.

### FR-04 · Listing management (M5)

- A listing offers one ticket at a fixed price. It stores the seller, event (copied from the ticket), asking price, original price, status, expiry time and who reviewed it.
- Lifecycle: `DRAFT → PENDING_VERIFICATION → ACTIVE → RESERVED → SOLD`, plus `REJECTED`, `CANCELLED` and `EXPIRED`. The full transition table is in `state-machines.md`.
- Rules:
  1. Only a `VERIFIED`, transfer-eligible, `ACTIVE` ticket owned by the caller can be listed.
  2. One ticket has at most one open listing, enforced by a partial unique index.
  3. A `RESERVED` listing cannot be reserved or bought by anyone else.
  4. A seller cannot reserve or buy their own listing.
  5. A seller cannot cancel a `RESERVED` listing; a transfer may be in progress.
  6. Asking price must be at least ₹1 and at most the original price. In `ACTIVE`, the price may only go down.
  7. Every status change writes an audit row in the same transaction.

**Test:** a table-driven test tries every (from, to) pair and only allowed ones succeed; a second open listing for one ticket fails; a price above the original gives 422; a failed change also rolls back its audit row.

### FR-05 · Marketplace discovery (M6)

- Anyone can browse published events and search `ACTIVE` listings by event name, and filter by category, city, price range and ticket category.
- Sort by listing date or price, both directions.
- Every list endpoint paginates with an opaque keyset cursor. Page size defaults to 20 and is capped at 100. Nothing loads all rows into memory.
- Public responses never include seller email, ticket documents or provider references.

**Test:** with 100,000 seeded listings, page 500 is as fast as page 1, and `EXPLAIN ANALYZE` shows no sequential scan on `listings` for common filters.

### FR-06 · Interest registration (M7)

- A buyer registers interest in an `ACTIVE` listing they do not own.
- Interest statuses: `ACTIVE`, `SELECTED`, `EXPIRED`, `WITHDRAWN`, `REJECTED`.
- A buyer holds at most one live (`ACTIVE` or `SELECTED`) interest per listing, enforced by a partial unique index.
- Only the seller and admins can see who is interested. Buyers never see each other.
- The seller triggers allocation with one of three strategies: first-come-first-served, random (with the candidate list and the result written to the audit log), or seller-selected. The winner becomes `SELECTED` and gets a reservation; the others stay `ACTIVE` as fallbacks.
- No bidding.

**Test:** a duplicate interest gives 409; the seller's own interest gives 403; FCFS picks the earliest; random picks are spread roughly evenly over 1,000 runs; the audit log alone explains every pick.

### FR-07 · Ticket reservation (M8)

- A reservation holds one listing for one buyer for 10 minutes.
- Reserving is one conditional `UPDATE` on the listing row (`ACTIVE` to `RESERVED`) plus an insert, in one transaction. The listing row is the lock target; the ticket row does not change until the transfer completes.
- Lifecycle: `CREATED → PAYMENT_PENDING → CONFIRMED`, plus `EXPIRED`, `FAILED` and `CANCELLED`.
- The expiry worker releases only `CREATED` reservations. A `PAYMENT_PENDING` reservation is resolved by asking the payment gateway, never released blindly.
- At most one live reservation per listing, enforced by a partial unique index as a safety net under the conditional update.

**Test:** 100 concurrent buyers produce exactly 1 reservation and 99 conflicts, 20 runs in a row; two expiry workers expire each reservation exactly once; expiry racing payment success never releases a paid listing.

### FR-08 · Order management (M9)

- An order records one purchase attempt: buyer, seller, listing, reservation, amount, currency, status and idempotency key.
- The amount is copied from the listing on the server. The client never sends a price.
- One order per reservation, enforced by a unique constraint on `reservation_id`.
- `POST /orders` requires an `Idempotency-Key` header. A replay with the same key and body returns the stored response; the same key with a different body gives 422.
- Status changes only through the order state machine.

**Test:** 50 concurrent requests with one key create one order and all get the same body; the same key with a different body gives 422; an expired or someone else's reservation is rejected.

### FR-09 · Payment processing (M10)

- A fake gateway, `fakepay`, simulates success, failure, delays, duplicate webhooks, bad signatures, timeouts and refunds, and exposes a lookup API for reconciliation.
- We save the payment row before calling the gateway and send the gateway an idempotency key, so a crash or retry never creates two payment intents.
- Webhooks are authenticated with HMAC-SHA256 over the raw body plus a timestamp, compared in constant time. Timestamps older than 5 minutes are rejected.
- Each provider event is stored once (`UNIQUE (provider, provider_event_id)`). A duplicate is answered with 200 and changes nothing.
- The webhook's amount and currency must match the order, or nothing changes and an alert is logged.
- Every received webhook is kept in `payment_events`; payment rows change only through the state machine and are never overwritten silently.
- A reconciliation job finds payments stuck pending, asks the gateway, and repairs local state through the same transition functions. Each run is recorded.
- No card data ever reaches our system.

**Test:** a webhook delivered 10 times changes state once; a forged signature gives 401 and changes nothing; a late webhook after reconciliation is a no-op; a timed-out payment leaves a recoverable order.

### FR-10 · Ticket transfer (M12)

- After payment succeeds, a worker asks the (fake) ticket provider to transfer the ticket.
- Transfer statuses: `INITIATED`, `PROCESSING`, `COMPLETED`, `FAILED`, `REQUIRES_MANUAL_REVIEW`.
- One transfer per order (`UNIQUE (order_id)`).
- On success, in one transaction: transfer `COMPLETED`, order `COMPLETED`, listing `SOLD`, ticket `current_owner_id` set to the buyer.
- On failure: automatic retries with backoff, then manual review. An admin may retry, or abandon, which refunds the buyer.
- A paid order is never `COMPLETED` before the transfer succeeds.

**Test:** the full happy path runs end to end; a duplicate payment-succeeded event creates one transfer; a permanent failure ends in manual review and then a refund.

### FR-11 · Notifications (M12)

- In-app notifications for listing verification, new reservation, payment success and failure, reservation expiry, ticket transfer, listing cancellation and dispute updates.
- Built by a consumer of outbox events; one event produces exactly one notification per affected user, even if delivered twice.
- Users read and mark only their own notifications.

**Test:** replaying the same event creates no second notification; user B cannot read user A's notifications.

### FR-12 · Audit and dispute management (M5, M13)

- Audit rows for listing changes, reservation creation and expiry, order creation, payment changes, transfers, refunds and every admin action. Each row is written in the same transaction as the change it records, and carries the request ID.
- `audit_logs` is append-only: the app never updates or deletes it.
- Buyer or seller of an order can open one dispute at a time, choose a reason, add evidence and follow its history. Admins resolve with refund-buyer, release-to-seller or reject.
- Dispute history is append-only (`dispute_events`).

**Test:** a rolled-back change leaves no audit row; every `/admin` route returns 403 to a USER (a test enumerates the router); a second open dispute on one order gives 409.

## Non-functional requirements

### NFR-01 · Consistency

Postgres is the only source of truth. Each guarantee has a database backstop, not just application code:

| Must prevent | Application check | Database backstop |
| --- | --- | --- |
| Double booking | conditional `UPDATE listings ... WHERE status = 'ACTIVE'` | partial unique index: one live reservation per listing |
| Duplicate active listings | check in service | partial unique index: one open listing per ticket |
| Duplicate orders | `Idempotency-Key` table | `orders.reservation_id UNIQUE` |
| Duplicate payment processing | webhook dedupe insert | `UNIQUE (provider, provider_event_id)` |
| Invalid transitions | `ALLOWED` transition map | `WHERE status = :from` in every update, `CHECK` on status values |

**Test:** the concurrency tests in M8 to M11, asserting on database rows, not on API responses.

### NFR-02 · Availability

Recover from an API crash, a broker outage, slow payment responses, dropped database connections and worker crashes. The tools are short transactions, the transactional outbox, idempotent consumers, `pool_pre_ping` and reconciliation.

**Test:** the M11 and M16 failure drills (stop RabbitMQ, kill a worker mid-task, restart Postgres) end with nothing lost and nothing applied twice.

### NFR-03 · Performance

Normal reads under 300 ms p95; reservation under 500 ms p95 under the test workload; no duplicate reservations under load; paginated queries stay fast as data grows. These are targets, measured and reported with the hardware used.

**Test:** Locust runs at 100 to 10,000 users (M16), read from Grafana dashboards (M15).

### NFR-04 · Security

Argon2id, JWT, roles, Pydantic validation on every input, SQLAlchemy bound parameters only (no SQL built from strings), Redis rate limits on login, listing creation, reservation, payment and search, a strict CORS allow-list, private storage with presigned URLs, HMAC webhooks, secrets only from the environment, and audit logs. No card data, ever.

**Test:** the auth, IDOR, webhook and rate-limit tests; a security pass in M17.

### NFR-05 · Maintainability

A modular monolith: each module owns its tables and exposes service functions; business rules do not import FastAPI; dependencies are injected with `Depends`; Alembic for every schema change; one error format (RFC 9457 problem details); automated tests; OpenAPI docs at `/docs`.

**Test:** CI runs ruff and pytest on every push (from M3).

### NFR-06 · Observability

JSON logs with a request ID from M1. From M15: Prometheus metrics for request rate, latency, errors, database pool use, queue depth, outbox backlog, worker failures, payment failures and reservation expiries; Grafana dashboards; OpenTelemetry traces from API to worker. Never log passwords, tokens, payment secrets or ticket credentials.

**Test:** every log line in the M1 test suite parses as JSON and carries the request ID; in M15 a load test is readable from the dashboards alone.
