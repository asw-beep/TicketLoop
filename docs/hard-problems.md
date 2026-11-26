# The six hard problems, as SQL

For each problem: the exact statements in order, why they are correct, and what happens if the process dies between any two of them. Table and column names match [`schema-draft.sql`](schema-draft.sql). `:name` marks a bound parameter.

| # | Problem | Main mechanism | Database backstop | Module |
| --- | --- | --- | --- | --- |
| 1 | Double booking | Conditional `UPDATE ... WHERE status = 'ACTIVE'` | `uq_reservations_one_live_per_listing` | M7 (built) |
| 2 | Duplicate orders | `idempotency_keys` row in the same transaction | `uq_orders_reservation` | M8 |
| 3 | Duplicate or forged webhooks | HMAC check, then dedupe insert in the same transaction as the effect | `uq_payment_events_provider_event` | M8 |
| 4 | Lost events | Transactional outbox plus idempotent consumers | `pk_processed_events` | M9 |
| 5 | Expiry racing payment | Both sides use conditional updates on `status = 'CREATED'`; `PAYMENT_PENDING` is resolved by asking the gateway | row lock on the reservation | M7 (expiry), M8 |
| 6 | Drift | Reconciliation through the same transition functions, recorded per run | conditional updates make re-runs no-ops | M8 |

Two Postgres facts the answers lean on:

- **Read Committed (the default) and concurrent updates.** If transaction B tries to `UPDATE` a row that transaction A has updated but not committed, B waits. When A commits, B re-checks its `WHERE` clause against A's new version of the row. If the row no longer matches, B updates nothing. If A rolled back, B proceeds against the original row.
- **`now()` is the transaction's start time,** constant for the whole transaction. Keep transactions short, or a long one compares against a stale clock. `clock_timestamp()` is the real current time if you ever need it.

---

## 1 · Double booking: 100 buyers, one listing

**Goal:** exactly one reservation, 99 clean 409s.

```sql
BEGIN;  -- READ COMMITTED

-- S1. Claim the listing. The WHERE clause IS the availability check.
UPDATE listings
   SET status = 'RESERVED', updated_at = now()
 WHERE id = :listing_id
   AND status = 'ACTIVE'
   AND seller_id <> :buyer_id
   AND expires_at > now()
RETURNING id;
-- 0 rows: ROLLBACK and return 409 (or 200 if this buyer already holds the live reservation).

-- S2. Create the hold.
INSERT INTO reservations (listing_id, buyer_id, status, expires_at)
VALUES (:listing_id, :buyer_id, 'CREATED', now() + interval '10 minutes')
RETURNING id, expires_at;

-- S3. Audit, and from M9 the outbox event that notifies the seller.
INSERT INTO audit_logs (actor_id, entity_type, entity_id, action, request_id, details)
VALUES (:buyer_id, 'listing', :listing_id, 'listing.reserved', :request_id,
        jsonb_build_object('reservation_id', :reservation_id));
INSERT INTO outbox_events (aggregate_type, aggregate_id, event_type, payload)
VALUES ('reservation', :reservation_id, 'reservation.created', :payload);

COMMIT;
```

**Why it works.** Every transaction that reaches S1 runs it against the same row. The first takes the row lock; the others queue behind it. When the first commits, each waiting transaction re-checks `status = 'ACTIVE'`, finds `RESERVED`, and updates zero rows. The app does read the listing before S1, to answer 404, 403 and the commonest 409s without a lock, but that read decides nothing: whatever it saw, S1 checks again as it writes, so there is no gap between check and write.

**The backstop.** If a future code path forgot S1 and inserted a reservation directly, `uq_reservations_one_live_per_listing` would reject the second live reservation.

| Process dies after | Database state | Outcome |
| --- | --- | --- |
| S1, S2 or S3 (before COMMIT) | Postgres rolls the whole transaction back when the connection drops | Listing still `ACTIVE`. Nothing leaked. |
| COMMIT, before the response reaches the client | Reservation exists | Client sees a timeout and retries; S1 matches 0 rows, but this buyer holds the live reservation, so the API returns it with 200. |

**Built in M7.** `tests/concurrency/test_reservation_race.py`: four API processes, 100 threads with 100 different users firing at once. 1 × 201, 99 × 409, exactly one live reservation row, listing `RESERVED`, one audit row each; 20 rounds in a row. The comparison with `FOR UPDATE` and `SERIALIZABLE` is in [`reservation-design.md`](reservation-design.md).

---

## 2 · Duplicate orders: the retried POST

**Goal:** `POST /orders` with the same `Idempotency-Key` creates one order however many times it arrives, even concurrently.

```sql
BEGIN;

-- S1. Claim the key. A concurrent request with the same key BLOCKS here until we finish.
INSERT INTO idempotency_keys
       (user_id, key, request_method, request_path, request_hash, expires_at)
VALUES (:user_id, :key, 'POST', '/api/v1/orders', :request_hash, now() + interval '24 hours')
ON CONFLICT (user_id, key) DO NOTHING
RETURNING key;

-- 0 rows: the key was used before. Read what is stored, ROLLBACK, and answer from it:
SELECT request_hash, response_status, response_body
  FROM idempotency_keys
 WHERE user_id = :user_id AND key = :key;
--   request_hash differs  -> 422 (same key, different request: a client bug)
--   otherwise             -> replay response_status + response_body

-- S2. Check and lock the reservation, reading the price from the database.
SELECT r.listing_id, l.seller_id, l.asking_price_paise
  FROM reservations r
  JOIN listings l ON l.id = r.listing_id
 WHERE r.id = :reservation_id
   AND r.buyer_id = :user_id
   AND r.status = 'CREATED'
   AND r.expires_at > now()
   FOR UPDATE OF r;  -- the expiry worker cannot expire it until we commit
-- 0 rows: ROLLBACK, 404 if it is not this buyer's, else 409.

-- S3. Create the order. The amount comes from S2, never from the request.
INSERT INTO orders (reservation_id, listing_id, buyer_id, seller_id,
                    amount_paise, currency, status, idempotency_key)
VALUES (:reservation_id, :listing_id, :user_id, :seller_id,
        :asking_price_paise, 'INR', 'CREATED', :key)
RETURNING *;
-- uq_orders_reservation violation: an order already exists under a different key -> 409.

-- S4. Store the response under the key, in the same transaction.
UPDATE idempotency_keys
   SET response_status = 201, response_body = :response_json
 WHERE user_id = :user_id AND key = :key;

-- S5. Audit.
INSERT INTO audit_logs (actor_id, entity_type, entity_id, action, request_id, details)
VALUES (:user_id, 'order', :order_id, 'order.created', :request_id, '{}');

COMMIT;
```

**Why it works.** The key row, the order and the stored response commit together or not at all. A concurrent duplicate's S1 waits on our uncommitted key row (a unique index makes the second insert wait for the first transaction). When we commit, its insert conflicts, it reads our stored response and replays it. If we roll back, its insert succeeds and it does the work itself.

**Two layers, different jobs.**

- The key table catches the **same request sent twice** and gives the client the identical response.
- `uq_orders_reservation` catches **a different request for the same reservation**: a buggy client that makes a new key per retry, or a second browser tab.

| Process dies after | Database state | Outcome |
| --- | --- | --- |
| S1 to S5 (before COMMIT) | Everything rolled back, key row included | The client retries with the same key and the work runs fresh. |
| COMMIT, before the response | Order and stored response exist | The retry replays the stored 201 body. One order. |

`POST /payments/intents` cannot hold one transaction open across an HTTP call to the gateway, so it uses the key in two phases: commit the key row with `locked_at = now()`, call the gateway, then store the response and clear `locked_at`. A request that finds the key locked gets 409 and retries later; a lock older than 2 minutes is treated as abandoned.

**Built in M8.** `app/modules/idempotency/service.py` is the key table; `tests/concurrency/test_payment_race.py` sends one request 50 times at once to four API processes: one order, 50 identical bodies, 49 of them marked as replays. Fifty different keys for one reservation: one 201 and 49 `order-exists`. The order's own insert sits behind the reservation's lock, so `uq_orders_reservation` is a safety net nothing reaches. A request that fails stores nothing under its key.

---

## 3 · Duplicate or forged webhooks

**Goal:** a forged webhook changes nothing; a genuine one delivered ten times takes effect once.

```text
V1. signature = hex(HMAC_SHA256(secret, timestamp + "." + raw_body))
    hmac.compare_digest(signature, header_signature)  else 401, stop.
V2. abs(now - timestamp) <= 300 seconds                else 401, stop.
V3. Only now parse the JSON.
```

```sql
BEGIN;

-- S1. Dedupe: the first delivery inserts, every later one does not.
INSERT INTO payment_events (provider, provider_event_id, event_type, payload, processing_status)
VALUES ('fakepay', :event_id, :event_type, :payload, 'RECEIVED')
ON CONFLICT (provider, provider_event_id) DO NOTHING
RETURNING id;
-- 0 rows: duplicate. COMMIT and return 200 without touching anything else.

-- S2. Load and lock the payment.
SELECT id, order_id, status, amount_paise, currency
  FROM payments
 WHERE provider = 'fakepay' AND provider_transaction_id = :transaction_id
   FOR UPDATE;
-- Amount or currency differs from the webhook: mark the event FAILED, alert, COMMIT, 200.

-- S3. Apply the effect with conditional updates.
UPDATE payments
   SET status = 'SUCCEEDED', provider_response = :payload, updated_at = now()
 WHERE id = :payment_id AND status IN ('CREATED', 'PENDING')
RETURNING id;
-- 0 rows: already final (reconciliation got here first). Mark the event IGNORED, COMMIT, 200.

UPDATE orders       SET status = 'PAID',      updated_at = now()
 WHERE id = :order_id AND status = 'PAYMENT_PENDING';
UPDATE reservations SET status = 'CONFIRMED', updated_at = now()
 WHERE id = :reservation_id AND status = 'PAYMENT_PENDING';

-- S4. Outbox and audit.
INSERT INTO outbox_events (aggregate_type, aggregate_id, event_type, payload)
VALUES ('order', :order_id, 'payment.succeeded', :payload);
INSERT INTO audit_logs (actor_id, entity_type, entity_id, action, details)
VALUES (NULL, 'payment', :payment_id, 'payment.succeeded', :details);

-- S5. Close the event.
UPDATE payment_events
   SET processing_status = 'PROCESSED', payment_id = :payment_id, processed_at = now()
 WHERE id = :payment_event_id;

COMMIT;  -- then return 200
```

**Why it works.**

- **Forgery:** without the secret nobody can produce a valid HMAC. It is computed over the raw bytes, because parsing and re-serialising JSON can change whitespace or key order and break the signature. `compare_digest` takes the same time whether the first byte or the last one differs, so response timing leaks nothing.
- **Replay of an old genuine webhook:** the 5-minute timestamp window rejects it; inside the window the dedupe row does.
- **Duplicates:** the dedupe row and the effect commit in one transaction. Either both exist, or neither does and the gateway's retry processes it fresh.
- **200 for duplicates:** any non-2xx tells the gateway "try again". Answering a duplicate with an error would make it retry forever.

| Process dies after | Database state | Outcome |
| --- | --- | --- |
| V1 to V3 | Nothing written | No 200 sent, so the gateway retries. |
| S1 to S5 (before COMMIT) | Rolled back, dedupe row included | The gateway retries; processed fresh. |
| COMMIT, before the 200 | Effect and dedupe row committed | The gateway retries; S1 conflicts; 200; nothing changes twice. |

**Built in M8.** The one difference from the sketch: the effect is not three loose updates but `succeed()` or `fail()`, which lock the reservation first and go through the state machines, inside a savepoint, so an event that cannot be applied is kept as `FAILED` with its reason instead of becoming a 500 the gateway retries for ever. The outbox row arrives with M9. Tests: one webhook delivered 10 times at once, one state change, one event row, ten 200s; a forged signature, an old timestamp, a changed byte: 401 and no rows; a late webhook after reconciliation: `IGNORED`.

---

## 4 · Lost events: the database commits, RabbitMQ is down

**Goal:** every committed state change reaches its consumers eventually, even through a broker outage, and is applied once.

**The dual-write trap.** "Commit, then publish to RabbitMQ" loses the event if the process dies between the two. "Publish, then commit" announces a change that may then roll back. Two systems cannot be written atomically. So write only to Postgres, and publish later from there.

**Write side.** Inside the business transaction (problem 3, S4), insert into `outbox_events`. The event exists exactly when the change exists.

**Relay** (a loop, any number of copies):

```sql
BEGIN;

-- R1. Take a batch nobody else is working on.
SELECT id, event_type, payload
  FROM outbox_events
 WHERE published_at IS NULL AND next_attempt_at <= now()
 ORDER BY created_at
 LIMIT 100
   FOR UPDATE SKIP LOCKED;

-- R2. (Python) Publish each row with publisher confirms, message_id = outbox id.
--     Wait for the broker's ack before counting it as sent.

-- R3. Mark what the broker confirmed.
UPDATE outbox_events SET published_at = now()
 WHERE id = ANY(:acked_ids);

-- R4. Back off on the rest.
UPDATE outbox_events
   SET retry_count = retry_count + 1,
       next_attempt_at = now() + :backoff,   -- exponential with jitter, capped
       last_error = :error
 WHERE id = ANY(:failed_ids);

COMMIT;
```

`SKIP LOCKED` lets several relays run at once: each skips rows another relay has locked instead of waiting for them.

**Consumer** (a Celery task with `acks_late=True`, so the message is acknowledged only after the task finishes):

```sql
BEGIN;

-- C1. Have I seen this event before?
INSERT INTO processed_events (consumer, event_id)
VALUES ('transfers.start', :event_id)
ON CONFLICT DO NOTHING
RETURNING event_id;
-- 0 rows: already done. COMMIT and ack.

-- C2. Do the work, itself guarded by constraints and conditional updates.
INSERT INTO transfers (order_id, ticket_id, from_user_id, to_user_id, provider_id, status)
VALUES (:order_id, :ticket_id, :seller_id, :buyer_id, :provider_id, 'INITIATED')
ON CONFLICT (order_id) DO NOTHING;
UPDATE orders SET status = 'TRANSFER_PENDING', updated_at = now()
 WHERE id = :order_id AND status = 'PAID';

COMMIT;  -- then ack the message
```

| Process dies after | Database state | Outcome |
| --- | --- | --- |
| R2 (broker acked) before R3 commits | Rows still unpublished | Published again next loop: a **duplicate message**. C1 makes it harmless. |
| Broker down during R2 | R4 records the failure | Rows wait with growing backoff. Nothing is lost; all are published after the broker returns. |
| C2, before COMMIT | Rolled back; message not acked | RabbitMQ redelivers; processed fresh. |
| COMMIT, before the ack | Work and `processed_events` row committed | Redelivered; C1 conflicts; acked; nothing done twice. |

**At-least-once delivery plus idempotent consumers gives an exactly-once effect.** Exactly-once delivery itself is impossible when either side can crash between doing and acknowledging.

**Test (M9):** stop RabbitMQ, make payments, watch outbox rows pile up, start RabbitMQ, see all published. `docker kill` a worker mid-task: the message is redelivered and skipped.

---

## 5 · Expiry racing payment

**Goal:** the expiry worker never releases a listing that was just paid for, or whose payment is unresolved (FR-07).

**Payment side** (`POST /payments/intents`, after the idempotency step):

```sql
BEGIN;

-- P1. Move the reservation out of CREATED. Only works if it is still CREATED and unexpired.
UPDATE reservations
   SET status = 'PAYMENT_PENDING', updated_at = now()
 WHERE id = :reservation_id
   AND buyer_id = :user_id
   AND status = 'CREATED'
   AND expires_at > now()
RETURNING id;
-- 0 rows: ROLLBACK, 409 "reservation expired". No money has moved yet.

-- P2.
UPDATE orders SET status = 'PAYMENT_PENDING', updated_at = now()
 WHERE id = :order_id AND status = 'CREATED';

-- P3. Save the payment before talking to the gateway.
INSERT INTO payments (id, order_id, provider, amount_paise, currency, status, idempotency_key)
VALUES (:payment_id, :order_id, 'fakepay', :amount_paise, 'INR', 'CREATED', :payment_id::text);

COMMIT;

-- P4. (HTTP, outside any transaction) Create the gateway intent with
--     Idempotency-Key = payment_id and an intent expiry of 15 minutes.

-- P5.
UPDATE payments
   SET status = 'PENDING', provider_transaction_id = :txn_id,
       provider_response = :response, updated_at = now()
 WHERE id = :payment_id AND status = 'CREATED';
```

**Expiry side** (worker loop, every 30 seconds):

```sql
BEGIN;

-- E1. Claim due CREATED reservations. PAYMENT_PENDING is deliberately NOT in this scan.
SELECT id, listing_id
  FROM reservations
 WHERE status = 'CREATED' AND expires_at <= now()
 ORDER BY expires_at
 LIMIT 100
   FOR UPDATE SKIP LOCKED;

-- E2. For each claimed row:
UPDATE reservations SET status = 'EXPIRED', updated_at = now()
 WHERE id = :reservation_id AND status = 'CREATED';
UPDATE listings
   SET status = CASE WHEN expires_at > now() THEN 'ACTIVE' ELSE 'EXPIRED' END,
       updated_at = now()
 WHERE id = :listing_id AND status = 'RESERVED';
UPDATE orders SET status = 'CANCELLED', updated_at = now()
 WHERE reservation_id = :reservation_id AND status = 'CREATED';
INSERT INTO audit_logs (...) VALUES (...);    -- reservation.expired
INSERT INTO outbox_events (...) VALUES (...); -- notify buyer and seller

COMMIT;
```

**Why it works.** P1 and E2 both change the same reservation row, and both require `status = 'CREATED'`. The row lock lets only one of them go first:

- **Expiry first:** E1 holds the row lock. P1 waits, then re-checks, finds `EXPIRED`, updates nothing, returns 409. The gateway was never called, so no money moved.
- **Payment first:** P1 holds the lock. E1's `SKIP LOCKED` skips the row; on the next pass it is `PAYMENT_PENDING` and not in the scan at all.

The gateway is called only after P1 commits, so money can move only for a reservation that is already safe from the clock.

**Resolving `PAYMENT_PENDING`.** A second scan picks up `PAYMENT_PENDING` reservations older than `expires_at + 15 minutes` and **asks the gateway** about each one:

| Gateway says | Action |
| --- | --- |
| Paid | Apply success through the same function the webhook uses. |
| Failed, or intent expired unpaid | Payment `FAILED` (`timeout`), reservation `EXPIRED`, order `CANCELLED`, listing released. Safe, because the 15-minute intent expiry means the gateway itself will refuse a late payment. |
| No answer | Do nothing. Try again next pass. Never release on silence. |

**Last line of defence.** If money still arrives for a payment we already marked `FAILED`, it changes nothing: the event is kept as `IGNORED` and an error is logged for a person to look at. Refunds are out of this project's scope, and fakepay refuses to take money for an intent that was declined or has expired, so this should never happen.

| Process dies after | Database state | Outcome |
| --- | --- | --- |
| P3 commit, before P4 | Payment `CREATED`, no gateway intent | Reconciliation (problem 6) repeats P4 with the same idempotency key, or fails the payment if the buyer has gone. |
| P4, before P5 | Gateway intent exists, we never saved its id | Reconciliation repeats P4 with the same key; the gateway returns the **same** intent rather than a second one. |
| E2, before COMMIT | Rolled back | The next pass expires it again. |

**Tests.** M7: two expiry workers at once expire each reservation exactly once (count audit rows), and a cancel against an expiry releases the listing once. M8: expiry racing a payment's success, 50 rounds, never releases a paid listing, and both outcomes happen; checkout racing the clock and racing **Cancel hold** has one winner; the buyer coming back while the overdue check is running keeps the hold.

---

## 6 · Drift: the gateway and the database disagree

**Goal:** find every payment where our records and the gateway's differ, and repair it, safely enough to run every two minutes.

**Causes:** a webhook never arrives, arrives after a crash, or a crash lands between two steps of problems 3 or 5.

```sql
-- G1. Open a run (its own short transaction).
INSERT INTO reconciliation_runs (id, status) VALUES (:run_id, 'RUNNING');

-- G2. Find candidates.
SELECT id, provider_transaction_id, status, order_id
  FROM payments
 WHERE status IN ('CREATED', 'PENDING')
   AND updated_at <= now() - interval '5 minutes'  -- long past any request still running
 ORDER BY updated_at
 LIMIT 500;
```

For each candidate, ask the gateway (by transaction id, or by our idempotency key when the transaction id was never saved). Then, **each fix in its own transaction**, call the same transition functions the webhook uses:

| Local | Gateway | Fix |
| --- | --- | --- |
| `PENDING` | succeeded | `succeed(payment_id, source='reconciliation')`, the function the webhook calls |
| `PENDING` | failed | `fail(..., reason='declined')` |
| `PENDING` | expired unpaid | `fail(..., reason='timeout')`: order `CANCELLED`, reservation `EXPIRED` |
| `PENDING` | never heard of it | Nothing, reported every run: it gave us that id, so this is a fault and not "unpaid" |
| `CREATED` | intent exists | Save the transaction id, then whichever of the above applies |
| `CREATED` | no such intent | `fail(..., reason='intent_never_created')`, but only once the hold has ended (until then the buyer may try again, and that try makes the intent), and not if the buyer has touched the payment since we looked |
| either | another amount | Nothing; reported and logged as an error |
| either | no answer | Nothing; reported for that payment and the run is `FAILED`, but the run goes on. Three unanswered lookups in a row stop it: the gateway is down, and asking about the rest would only time out again |

A run can take a while. Each candidate is looked at again just before its turn, and one its buyer has touched since the run began is left for the next run.

A payment that succeeded with an order still unpaid cannot exist: `succeed()` moves both in one transaction.

```sql
-- G3. Close the run.
UPDATE reconciliation_runs
   SET status = 'SUCCEEDED', finished_at = now(),
       checked_count = :checked, fixed_count = :fixed, findings = :findings
 WHERE id = :run_id;
```

**Why it is safe to repeat.** Every fix is a conditional transition. A second run finds the payment already final, its update matches 0 rows, and it records nothing to fix. A late webhook arriving after a fix hits the same conditional update and is marked `IGNORED`.

**Why it is auditable.** Every run leaves a row listing each mismatch, what the gateway said and what was done (`findings`), and every fix writes an audit row with `source = 'reconciliation'`.

| Process dies after | Database state | Outcome |
| --- | --- | --- |
| G1 | A run stuck `RUNNING` | The next run marks stale `RUNNING` rows (older than 30 minutes) `FAILED`, then starts fresh. |
| Some fixes committed | Those payments are final | The next run skips them and fixes the rest. |

**Built in M8.** `scripts/reconcile_payments.py`, every 2 minutes by Celery beat from M9. The gateway is made to "forget" the webhooks of 20 payments: one run fixes all 20, a second fixes 0. Runs at once, and a run racing the late webhook, settle each payment once.
