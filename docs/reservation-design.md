# Reservation design: how one buyer wins

A hundred buyers press **Reserve** on one ticket at the same moment. Exactly one must get it, the other 99 must get a clean answer, and it must hold with several API processes and any of them dying at any point. This is hard problem 1 in [`hard-problems.md`](hard-problems.md); this document records how it is solved, what else was tried, and what each cost.

## What the app does

One transaction, at Postgres's default Read Committed:

1. `UPDATE listings SET status = 'RESERVED' WHERE id = :id AND status = 'ACTIVE' AND seller_id <> :buyer AND expires_at > now()`, through the listing state machine. It either changes one row or none.
2. The listing's audit row.
3. `INSERT INTO reservations (..., expires_at) VALUES (..., now() + interval '10 minutes')`.
4. The reservation's audit row. Commit.

The first buyer's `UPDATE` takes the row lock. The others' `UPDATE`s wait for it. When it commits, Postgres re-checks each waiting `WHERE` against the new version of the row, finds `status = 'RESERVED'`, and updates nothing. No row means 409, or 200 with the hold they have if it is the same buyer asking again.

**The safety net.** `uq_reservations_one_live_per_listing`, a partial unique index over the statuses `CREATED`, `PAYMENT_PENDING` and `CONFIRMED`, refuses a second live reservation whatever the code does.

**Why not Redis.** A lock in Redis can be lost (a failover, an expiry during a pause) and Postgres would still have to be asked who won. Nothing that decides who gets a ticket reads Redis.

## The alternatives

Both live in `tests/concurrency/strategies.py`, never in `app/`. The same harness races all three.

- **`SELECT … FOR UPDATE`, then check in Python, then write.** Pessimistic. Correct, and easy to read. It holds the lock across extra round trips, and every loser takes the lock too, one after the other, just to read that it lost.
- **`SERIALIZABLE` with a retry.** Optimistic. No lock and no condition in the `UPDATE`; Postgres aborts any transaction whose reads another one invalidated (SQLSTATE 40001) and the code runs it again. Correct only with that retry loop around every caller.

## Results

<!-- results:start -->

Written by `uv run python -m tests.concurrency.report`; do not edit by hand.

- **Race:** 100 buyers, one listing, released together, 20 rounds per strategy after 3 to warm up. Every round of all three ended with exactly one winner, checked in the database.
- **Server:** 4 API processes, 15 database connections each at most.
- **Where:** Windows 11, Intel64 Family 6 Model 183 Stepping 1, GenuineIntel, 16 logical CPUs; PostgreSQL 17.12 (Debian 17.12-1.pgdg13+1) in Docker. The buyers, the API and the database share the one machine.
- **Times:** measured by the buyer, queueing included.

| Strategy | Won | 409 | Retried | p50 | p95 | Slowest | Winner's median |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Conditional `UPDATE` (the app) | 20 | 1,980 | 0 | 340 ms | 460 ms | 556 ms | 122 ms |
| `SELECT … FOR UPDATE`, check in Python | 20 | 1,980 | 0 | 656 ms | 1192 ms | 1459 ms | 135 ms |
| `SERIALIZABLE`, retry on 40001 | 20 | 1,980 | 128 | 436 ms | 549 ms | 662 ms | 150 ms |

<!-- results:end -->

## Reading them

All three are correct, so the choice is about cost and about how easy each is to get wrong.

- **The conditional `UPDATE` does the check and the write in one statement.** It holds the row lock for the shortest time and never repeats work. A loser usually finds out from its first read, which needs no lock at all.
- **The row lock serialises the losers as well.** Each waits its turn for a lock it will not use, so the slowest buyers wait for everyone before them: twice the median and well over twice the p95.
- **`SERIALIZABLE` is close, and pays in retries.** A loser that had already read `ACTIVE` aborts and runs its whole transaction again; on this machine about six buyers a round. The retry must wrap the transaction, so it cannot live in a service function: every caller has to know, and forgetting it turns a conflict into a 500.
- **The times are for comparing the three, not a promise about speed.** A hundred requests at once on four Python processes mostly queue for a process.

The app uses the conditional `UPDATE`. `FOR UPDATE` is the right tool where a decision needs several rows to stay still, and the app does use it there: cancelling an event locks all the event's open listings first, and the expiry job claims due reservations with `SKIP LOCKED`.

## Around the race

| Race | What decides it |
| --- | --- |
| A buyer double-clicks | The second request's `UPDATE` matches nothing; it finds the buyer's own live reservation and answers 200 with it. |
| The seller cancels while buyers reserve | Both are conditional `UPDATE`s on the same row and the same `status = 'ACTIVE'`. One matches. |
| An admin cancels the event while a buyer reserves | The cancel locks every open listing of the event, `RESERVED` ones included, in id order. If it finds one reserved it refuses (409 `event-has-reservations`). Locking only the cancellable ones would skip a listing being reserved at that moment and leave a cancelled event with a held ticket; the race test fails if the code does that. |
| The hold runs out while the buyer cancels | Both move the reservation from `CREATED` with a conditional `UPDATE`. One matches, and only that one releases the listing. |
| Two expiry workers | `FOR UPDATE SKIP LOCKED` gives each due reservation to one of them. |
| The hold runs out while the buyer pays (M8) | Both move the reservation from `CREATED`; `PAYMENT_PENDING` is out of the clock's reach. |

**Locks are taken in one order**: a reservation before its listing, and several listings in id order. Two transactions that follow it can wait for each other but never in a circle. Reserving takes its listing first and cannot deadlock: the reservation it inserts does not exist for anyone else to have locked. The second half of the rule was learned: an expiry pass used to release listings in the order their holds ended, and an admin cancelling or moving the event in the middle of a pass got a deadlock. `test_an_expiry_pass_against_a_change_to_the_event_never_deadlocks` runs all three.

## Running it

```bash
docker compose up -d db
uv run pytest tests/concurrency                                              # every race, 3 rounds
RACE_ROUNDS=20 uv run pytest tests/concurrency/test_reservation_race.py      # the 20-run proof
uv run python -m tests.concurrency.report                                    # the table above
```

The harness starts four API processes on ports of their own rather than `uvicorn --workers 4`: on Windows, requests to a socket shared by several uvicorn workers sometimes never got an answer. Each process has its own interpreter and connection pool, which is what the test is about.
