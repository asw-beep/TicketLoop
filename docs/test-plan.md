# Load and contention test plan (M12)

Written before any of it ran. For each test: what I expect and why, the command that runs it, what I measure, and the line between pass and fail. The results, with every miss explained, are in [`test-results.md`](test-results.md).

## The machine, the data, the configuration

Every number belongs to this setup and says nothing about any other.

| | |
| --- | --- |
| Machine | Laptop, Intel Core i5-13450HX (10 cores, 16 threads), 23.7 GB RAM, Windows 11 |
| Docker | Docker Desktop on WSL 2, given all 16 threads and 11.5 GiB |
| Where k6 runs | In the same Docker VM, so it competes with the system under test for the same CPUs |
| Data | `scripts/seed_discovery.py`: 2,000 events, 100,000 listings from 2,000 sellers, 1,000 buyers |
| API | 4 containers, each one uvicorn process; sync routes on its 40-thread pool; a database pool of 5 + 10 overflow, plus one connection for `/metrics` |
| Worker | Celery prefork, 8 processes; one relay; one beat |
| Postgres | 17, `max_connections=200`, otherwise the image's defaults |
| Redis | 8.4: the search cache (30 to 60 s) and the rate limits |
| Rate limits | Raised to 1,000,000 a minute for every run but one: k6 is one address playing 1,000 users. The limits run uses the real ones |
| Tracing | On, one trace in a hundred (`OTEL_TRACES_SAMPLER=parentbased_traceidratio`, `0.01`): Jaeger keeps traces in memory |
| Stack | `docker-compose.load.yml` over `docker-compose.yml`, through `scripts/load_test.py` |

Start it:

```bash
docker compose up -d db
uv run python -m scripts.load_test seed      # recreates ticketloop_perf, migrates, seeds
uv run python -m scripts.load_test up        # the load stack; Prometheus finds the four API containers
```

Every run below ends with the invariants check. A run with even one violation fails, whatever its latencies.

## 1. The invariants, after every run

**Hypothesis.** Postgres enforces every rule that would mean a double sale (partial unique indexes, conditional `UPDATE`s, unique constraints), so no load or failure produces rows that break one.

**Script.** `scripts/check_invariants.sql`, run by `uv run python -m scripts.load_test invariants` and after every run below. One row per violation:

| Invariant | A violation is |
| --- | --- |
| `two-open-listings` | a ticket with two listings in `DRAFT`, `PENDING_VERIFICATION`, `ACTIVE` or `RESERVED` |
| `two-live-reservations` | a listing with two reservations in `CREATED`, `PAYMENT_PENDING` or `CONFIRMED` |
| `paid-order-not-paid` | a `SUCCEEDED` payment whose order is not `PAID`, `TRANSFER_PENDING`, `COMPLETED` or `TRANSFER_FAILED` |
| `owner-is-not-buyer` | a ticket whose latest completed order's buyer does not own it |
| `sold-twice` | a listing with two orders that were paid: the oversell itself |

**Pass.** Zero rows after every run. `tests/integration/test_invariants.py` proves each query finds its violation when one is planted.

## 2. Spike: 1,000 buyers, one listing, at once

**Hypothesis.** The reservation is one conditional `UPDATE` on the listing, backed by a partial unique index, so exactly one of 1,000 simultaneous buyers wins and the others get a clean `409 listing-not-available`. Nobody gets a 5xx, because losing is an expected answer, not an error.

**Script.** `uv run python -m scripts.load_test spike` (`tests/load/spike.js`). Setup logs in 1,000 buyers and takes the first listing that search returns. Then 1,000 virtual users each send one reservation at the same moment.

**Measure.** k6 counts 201s (`reserved`), 409s (`refused`), 5xx (`server_errors`) and anything else (`unexpected`). The runner counts that listing's live reservations in Postgres. Also measured: the latency of the losing answers.

**Pass.** `reserved` 1, `refused` 999, `server_errors` 0, `unexpected` 0; one live reservation in Postgres; invariants 0 rows.

## 3. Load: 100, 500 and 1,000 users

**Hypothesis.**
- **100 users:** every NFR-03 target holds. Reads are mostly cache hits, and a reservation is one short transaction.
- **500 users:** reads stay under target. Every purchase bumps the listings cache version, so the hit ratio falls as purchases rise.
- **1,000 users:** one laptop running the system and k6 runs out of CPU first. The p95s grow past target, while correctness doesn't move: no 5xx from contention, no oversell.

**Script.** `uv run python -m scripts.load_test load --vus N` (`tests/load/load.js`), for N = 100, 500, 1,000. Two scenarios ramp up over 30 s, hold for 3 minutes, and ramp down over 15 s:
- **Browse (80% of users):** search a city, open a listing, open its event, then think for 1 to 2 s.
- **Buy (20%):** search an event's tickets, reserve one, order it, start paying, and pay at fakepay with the outcome "succeed". Then wait for the order to turn `PAID`. A taken listing (409) ends the attempt.

**Measure.**
- From k6: requests per second; p50, p95 and p99 for reads and for reservations; error rate; time from paying to `PAID`.
- From Prometheus: database connections in use, the cache hit ratio, the outbox's oldest unpublished event and the queue depth (how late the worker is).
- From `docker stats` at the peak: CPU and memory per container.

**Pass.** Reads p95 under 300 ms, reservations p95 under 500 ms, errors under 1%, and invariants 0 rows. At 1,000 users a miss on latency is recorded as the finding it is, with what saturated. A miss on correctness is a failure at any stage.

## 4. The real limits, once

**Hypothesis.** From one address, search is held to 60 a minute: the rest get `429` with `Retry-After`, and nothing fails with a 5xx.

**Script.** `uv run python -m scripts.load_test limits` (`tests/load/limits.js`). The runner recreates the API containers with the real limits, runs 20 users searching without pause for one minute, then puts the raised limits back, even if the run failed.

**Measure.** Count of 429s with a `Retry-After` header (`limited`) and of 5xx.

**Pass.** `limited` above 0, `server_errors` 0.

## 5. Broker drill under load

**Hypothesis.** RabbitMQ is only downstream of the outbox. With it stopped, buyers still reserve, order and pay, and orders still turn `PAID`, because the webhook needs only Postgres. The outbox backlog climbs. Once RabbitMQ is back the relay drains it, and every paid order completes exactly once.

**Script.** `uv run python -m scripts.load_test drill-broker`. It runs 100 users for 4 minutes, stops RabbitMQ at 60 s and starts it again at 120 s. When k6 ends, it waits (up to 5 minutes) for the backlog to reach zero and for no order to be left `PAID` or `TRANSFER_PENDING`.

**Measure.** The backlog and its oldest event's age on the dashboard; orders by status at the end; the time to drain.

**Pass.** The backlog above 0 while RabbitMQ is down, and 0 after. No order left `PAID` or `TRANSFER_PENDING`. Invariants 0 rows, including `owner-is-not-buyer` and `sold-twice`.

## 6. The races, at scale

**Hypothesis.** The M7 and M8 races passed 20 and 50 rounds. They pass 100, because what decides them is a constraint and a conditional `UPDATE`, not timing.

**Script.** Four API processes on Windows against a database of their own (the M7 harness):

```bash
RACE_ROUNDS=100 uv run pytest tests/concurrency/test_reservation_race.py
RACE_PAID_ROUNDS=100 uv run pytest tests/concurrency/test_payment_race.py -k "never_released or against_the_clock"
```

**Measure.** Rounds passed, and the run's time.

**Pass.** Every round: one winner of 100 buyers; a paid ticket never released by the expiry job.
