# M8 Orders and payments Implementation Plan

> Executed inline, test first, one fresh review at the end, one commit `M8: Orders and payments`.

**Goal:** a held ticket is ordered and paid for exactly once, through a fake gateway that can fail, stall, repeat itself and lie, and every ending leaves the database correct.

**Architecture:** `app/modules/idempotency` (the key table and its flow), `app/modules/orders`, `app/modules/payments` (intents, webhook, settling, reconciliation), `app/infrastructure/payments` (the gateway client), and `fakepay/`, a separate FastAPI app that keeps its intents in SQLite and signs webhooks. Every status change goes through `StateMachine`.

**Spec:** `docs/superpowers/specs/2026-11-23-slim-roadmap-m6-m12-design.md`, M8; `docs/api-contract.md` Orders and Payments; `docs/state-machines.md`; `docs/hard-problems.md` 2, 3, 5, 6.

## Global Constraints

- Money in integer paise, INR only. The amount is copied from the listing on the server.
- Services never commit. One exception, written where it is: `POST /payments/intents` commits before it calls the gateway, because the payment must exist before money can move.
- **Lock order:** reservation, order, payment, listing. Everything that settles a payment locks the reservation row first; the reservation decides every race between paying, cancelling and the clock.
- No HTTP call inside a transaction.
- Secrets through the environment: `FAKEPAY_WEBHOOK_SECRET`, refused in staging and production with its built-in value.
- One 404 for missing and not yours; 403 `buyer-only` for someone who may see it but not act.

## Review Focus

1. The same click sent twice, at once or an hour later, with the same key, a new key, or after a crash between the two halves of `POST /payments/intents`: one order, one live payment, one intent at the gateway.
2. A webhook that is genuine but surprising: before we saved the transaction id, after reconciliation, for another amount, of an unknown type, with a huge or malformed body.
3. The ten minutes ending while the buyer is on the gateway's page; the fifteen-minute intent ending too. The buyer must never pay for a ticket that was released.
4. The gateway down, slow, or answering nonsense at each call: nothing is released on silence, and the buyer can try again.
5. Buttons pressed at the wrong time on screen: Checkout on a hold that just ran out, Back from the gateway, two tabs, a reload on the order page.

## Tasks

### 1. Tables, models, state machines

`idempotency_keys`, `orders`, `payments`, `payment_events`, `reconciliation_runs` as in `docs/schema-draft.sql`, without the refund statuses. Order statuses: `CREATED, PAYMENT_PENDING, PAID, PAYMENT_FAILED, CANCELLED, TRANSFER_PENDING, COMPLETED, TRANSFER_FAILED`. Payment statuses: `CREATED, PENDING, SUCCEEDED, FAILED`. Indexes: `uq_orders_reservation`, `ix_orders_buyer`, `ix_orders_seller`, `ix_orders_listing`, `uq_payments_one_live_per_order` (`CREATED, PENDING, SUCCEEDED`), `ix_payments_unresolved (updated_at)`, `uq_payments_provider_transaction`, `uq_payments_idempotency_key`, `uq_payment_events_provider_event`, `ix_payment_events_payment`, `ix_idempotency_keys_expiry`. Tests: the two transition tables, constraints, migration up and down.

### 2. Idempotency keys and `POST /orders`

`claim(db, user_id, key, request_hash, *, lock) -> StoredResponse | None`, `store(...)`, `release(...)`. Header missing 400 `idempotency-key-required`; not 1 to 255 printable ASCII characters 400 `idempotency-key-invalid`; other body 422 `idempotency-key-reused`; still running 409 `idempotency-key-in-progress`; replay carries `Idempotent-Replayed: true`. `POST /orders` in one transaction; `order-exists` 409 carries `order_id`; `reservation-not-payable` 409 with `current_status`. `GET /orders/{id}`, `GET /orders/me?role=&status=`. Cancelling or expiring a reservation cancels its `CREATED` order. `Reservation` gains `order_id`. Tests: replay, reuse, 50 at once, expired hold, someone else's, price copied, cancel and expiry cascade, access table.

### 3. `fakepay`

`POST /v1/intents` (idempotent on `Idempotency-Key`, 15-minute expiry), `GET /v1/intents/{transaction_id}`, `GET /v1/intents?idempotency_key=`, `POST /v1/intents/{id}/outcome`, `GET` and `POST /checkout/{id}`. Outcomes: `succeed, fail, delay, twice, bad_signature, no_webhook`. SQLite file, webhook sender with three tries. Tests through its own TestClient.

### 4. Gateway client and `POST /payments/intents`

`PaymentGateway.create_intent(...)`, `.find(transaction_id=|idempotency_key=)`; `GatewayUnavailable` (timeout, 5xx, nonsense) and `GatewayRefused` (4xx). Phase one commits the key lock, reservation and order `PAYMENT_PENDING`, payment `CREATED`; then the call; then `PENDING`, the stored response. Unavailable: key released, 503 `payment-gateway-unavailable`, and the next try resumes the same payment. Refused: payment, order and reservation fail and the listing goes back, 502 `payment-refused`. Asking again while a payment is open returns the same intent with 200.

### 5. Webhook

Signature, timestamp, then parse. 401 `bad-webhook-signature`, 400 `bad-webhook-body`, 404 provider. Insert the event `ON CONFLICT DO NOTHING`; settle in the same transaction; mark it `PROCESSED`, `IGNORED` or `FAILED`. Tests: once, ten times (in turn and at once), forged, stale, future, unknown transaction, mismatch, after reconciliation, late success on a failed payment.

### 6. Safe expiry and reconciliation

`settle_from_gateway(...)`, `resolve_overdue(...)` in the expiry script, `reconcile(...)` in `scripts/reconcile_payments.py` with a run row; also removes expired idempotency keys. Tests: every row of the table in hard problem 6; a second run fixes nothing; the races: 50 orders, cancel against checkout, expiry against checkout, resolve against a paid webhook, 50 rounds each way.

### 7. Screens

**Checkout** on the hold page; the order page (polls while `PAYMENT_PENDING`, **Go to the payment page**); **My orders**. Browser test: the purchase test goes on to Paid, then failed, then delayed.

### 8. Docs, review, fix pass, ship

Compose service `fakepay`, README, contract, database design, `documentation/m08-orders-and-payments.html`.
