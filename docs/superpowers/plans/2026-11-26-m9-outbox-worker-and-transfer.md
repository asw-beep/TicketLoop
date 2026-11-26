# M9 Outbox, worker and transfer Implementation Plan

> Executed inline, test first, one fresh review at the end, one commit `M9: Outbox, worker and transfer`.

**Goal:** a paid order ends with the ticket in the buyer's name, through events that survive a broker outage and a worker crash, with nothing lost and nothing applied twice.

**Architecture:** `app/modules/outbox` (the table, `emit()`, the relay loop), `app/modules/transfers` (the transfer state machine, the in-process provider adapter, the two consumer steps), `app/modules/notifications` (a second consumer and two routes), `app/workers` (the Celery app, its queues, the task wrappers and the beat schedule). Consumers are plain functions that take a session factory; the Celery tasks around them only translate outcomes into ack, retry or reject. RabbitMQ, the worker, beat and the relay run in Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-23-slim-roadmap-m6-m12-design.md`, M9; `docs/hard-problems.md` 4; `docs/state-machines.md` Order and Transfer; `docs/api-contract.md` Transfers and Notifications; `docs/schema-draft.sql` (`outbox_events`, `processed_events`, `transfers`, `notifications`).

## Global Constraints

- An outbox row is inserted in the transaction of the change it announces, never after it.
- Every consumer writes `processed_events (consumer, event_id)` in the same transaction as its effect. A message seen twice has one effect.
- No broker or provider call inside a transaction that holds a row lock longer than the call needs: the relay holds its claimed rows only while publishing with confirms; the transfer step commits `PROCESSING` before calling the provider.
- Lock order, extended: reservation, order, payment, transfer, listing, ticket.
- Status changes only through `StateMachine`, with audit rows.
- Pinned versions: `celery==5.7.0`, `rabbitmq:4.2.3-management-alpine`.
- Celery does not run on Windows in production; locally the tests start workers with `--pool=solo`.

## Rulings made in advance

- **Two extra event types.** `transfer.initiated` (internal: the step that calls the provider consumes it, so a crash between creating the transfer and calling the provider is recovered by redelivery rather than by a sweep) and `transfer.failed` (the buyer and seller are told it needs review). Spec lists four; these two are needed by the design.
- **Routing table in code.** `CONSUMERS: dict[event_type, tuple[consumer, ...]]`. The relay sends one Celery task per consumer per event to that consumer's queue, task id `"{event_id}:{consumer}"`.
- **Dead letters:** each queue has `x-dead-letter-exchange`; a task that gives up raises `Reject(requeue=False)`.
- **Who sees what after the sale.** The buyer becomes the ticket's owner and sees it under My tickets; the seller's evidence files and purchase reference stay with the original owner and admins.

## Review Focus

1. The broker down, slow, or refusing while rows are being published: rows wait with growing backoff and none is lost or marked published without a confirm.
2. A message delivered twice, at once, or after the worker died between commit and ack: one transfer, one ownership change, one notification per person.
3. The provider failing in each of its ways at each attempt, including the fifth: never two transfers, never a ticket moved without the order completing, never a retry after an unknown outcome.
4. A transfer that ends in manual review: the listing stays `RESERVED`, the event can't be cancelled, nobody can buy it, and the buyer's order page says what happened.
5. Two relays and two workers at once: every event published, each effect once.

## Tasks

### 1. Tables and models

Migration: `outbox_events` (with `headers`, `next_attempt_at`, `last_error`; `ix_outbox_unpublished (next_attempt_at, created_at) WHERE published_at IS NULL`), `processed_events (consumer, event_id)` PK, `transfers` (`uq_transfers_order`, `uq_transfers_provider_reference`, statuses `INITIATED, PROCESSING, COMPLETED, REQUIRES_MANUAL_REVIEW`, `ix_transfers_review_queue`), `notifications` (`uq_notifications_event_user`, `ix_notifications_user (user_id, created_at, id)`). Tests: constraints, migration up and down, `alembic check`.

### 2. `emit()` and the emitting places

`emit(db, aggregate_type, aggregate_id, event_type, payload) -> uuid.UUID`. Emitted by `succeed()` (`payment.succeeded`), `fail()` (`payment.failed` with reason), `expire_reservations()` (`reservation.expired`), the transfer steps. Tests: the row exists only if the change commits; each place emits once.

### 3. The relay

`relay_once(sessions, publish, *, batch=100) -> RelayReport`: claim due unpublished rows `FOR UPDATE SKIP LOCKED` in `created_at` order, publish each consumer's task, set `published_at`; a failure raises `retry_count`, sets `next_attempt_at = now() + backoff(retry_count)` (2^n seconds, capped at 5 minutes, jittered to 50–100%) and `last_error`, and leaves the rest of the batch for the next pass when the broker is down. `scripts`-free entry point `python -m app.workers.relay` (loop, `--once`). `CeleryPublisher` uses `send_task` with publisher confirms. Tests: in order; a failing publisher; not due skipped; two relays at once publish each row once; a confirm failure never marks a row published.

### 4. Transfers

State machine `INITIATED → PROCESSING → (PROCESSING | COMPLETED | REQUIRES_MANUAL_REVIEW)`, order `PAID → TRANSFER_PENDING → COMPLETED | TRANSFER_FAILED`, listing `RESERVED → SOLD`, ticket owner change. Adapter `TicketProvider.transfer(idempotency_key, ticket, to_user) -> reference` raising `RetryableTransferError`, `PermanentTransferError`, `UnknownTransferOutcome`; `FakeTicketProvider` with switches. `start_transfer(sessions, event)` and `perform_transfer(sessions, provider, event, attempt) -> Outcome` (DONE, RETRY, GAVE_UP). Tests: duplicate `payment.succeeded` makes one transfer; redelivered `transfer.initiated` after completion does nothing; success moves everything in one transaction; retryable then success; five retryable failures, permanent, unknown each end in review with the order `TRANSFER_FAILED`.

### 5. Notifications

`notify(sessions, event)`: one row per affected user per event, kinds per event type. `GET /notifications` (`unread_only`, keyset), `PATCH /notifications/{id}/read` (owner only, repeat harmless). Tests: once per user per event, a redelivery adds none, access.

### 6. Celery wiring, beat and compose

`app/workers/celery_app.py`: broker from `BROKER_URL`, confirms, `acks_late`, `reject_on_worker_lost`, prefetch 1, queues `transfers`, `notifications`, `maintenance` with dead-letter queues. Tasks wrap the consumers: retry with backoff and jitter up to 5, then `Reject(requeue=False)`. Beat: reservation expiry 30 s, listing expiry 5 min, reconciliation 2 min. Compose: `rabbitmq`, `worker`, `beat`, `relay`. CI: a RabbitMQ service container. Tests (broker): the full purchase through a real broker and a real worker ends `COMPLETED`; a killed worker's message is redelivered and applied once; a poison message ends in the dead-letter queue; the relay against a dead broker keeps its rows, then publishes them all.

### 7. Screens

The order page shows the transfer until **Completed** (or that it needs review); the buyer's **My tickets** shows the ticket; the seller's listing shows **Sold**. Browser test: the purchase goes on to Completed.

### 8. Docs, drills, review, fix pass, ship

Drill script `scripts/drill.py broker|worker|poison` against compose, its output on the documentation page; README, contract, state machines, hard problem 4, `documentation/m09-outbox-worker-and-transfer.html`.
