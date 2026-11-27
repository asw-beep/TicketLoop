# Walkthrough

Every feature by hand, in the browser and from a terminal, module by module. The [README](../README.md) has the one-command start and the 5-minute demo; this is the long way round. The commands assume the day-to-day setup in the README (the API on `localhost:8000`, the frontend on `localhost:5173`), or the one-command stack, which serves both on the same ports.

## Try the auth flow

Open http://localhost:5173: create an account, change your name, log out and in, and use **Forgot your password?**. The reset email lands in Mailpit at http://localhost:8025, and its link opens the reset page.

From a terminal instead (`-c` and `-b` keep the refresh cookie in a file, as a browser would):

```bash
B=localhost:8000/api/v1
curl -s $B/auth/register -H 'content-type: application/json' \
  -d '{"email":"asha@example.in","password":"correct horse battery staple","full_name":"Asha"}'
curl -s -c jar.txt $B/auth/login -H 'content-type: application/json' \
  -d '{"email":"asha@example.in","password":"correct horse battery staple"}'
# {"access_token":"eyJ...","token_type":"bearer","expires_in":900}  plus the refresh cookie
curl -s $B/users/me -H "Authorization: Bearer <access_token>"
curl -s -b jar.txt -c jar.txt -X POST $B/auth/refresh   # a new access token; the cookie rotates
```

Or open http://localhost:8000/docs, log in, and paste the access token into **Authorize**.

No route grants the admin role. Promote a registered account from the command line:

```bash
uv run python -m scripts.make_admin asha@example.in            # --revoke to undo
```

Set `JWT_SECRET_KEY` (at least 32 bytes) anywhere but your laptop; staging and production refuse to start with the built-in development key.

## Try the ticket flow

1. Create two accounts at http://localhost:5173/register, one for you as admin and one as a ticket holder (a private window keeps the sessions apart).
2. Promote the first: `uv run python -m scripts.make_admin <email>`, then reload the page. **Events**, **Verify tickets** and **Review listings** appear in the top bar.
3. **Events → New event**: fill it in with a start time in the future, create it (it starts as a draft), then **Publish**.
4. As the ticket holder, **My tickets → Register a ticket**: pick the event, enter the category, the price you paid in rupees, and the order and ticket numbers.
5. On the ticket's page, upload a PDF, PNG or JPEG up to 5 MB, then **Open** it: the file comes straight from MinIO through a link that works for 5 minutes.
6. As the admin, **Verify tickets**: open the same file, then **Verify** (with or without "Transfer eligible") or **Reject** with a reason. The holder's ticket page shows the decision.

The same from a terminal (it uses [`jq`](https://jqlang.org/) to pick fields out of the JSON), with `$ADMIN` and `$HOLDER` holding access tokens from `/auth/login`:

```bash
B=localhost:8000/api/v1
PROVIDER=$(curl -s $B/admin/providers -H "Authorization: Bearer $ADMIN" | jq -r '.[0].id')
EVENT=$(curl -s $B/admin/events -H "Authorization: Bearer $ADMIN" -H 'content-type: application/json' \
  -d "{\"name\":\"Monsoon Beats\",\"category\":\"CONCERT\",\"venue\":\"Stadium\",\"city\":\"Kochi\",
       \"starts_at\":\"2027-01-15T13:00:00Z\",\"organizer\":\"Backwater Live\",\"provider_id\":\"$PROVIDER\"}" | jq -r .id)
curl -s -X PATCH $B/admin/events/$EVENT -H "Authorization: Bearer $ADMIN" \
  -H 'content-type: application/json' -d '{"status":"PUBLISHED"}'
TICKET=$(curl -s $B/tickets -H "Authorization: Bearer $HOLDER" -H 'content-type: application/json' \
  -d "{\"event_id\":\"$EVENT\",\"ticket_category\":\"GENERAL\",\"original_price_paise\":250000,
       \"purchase_reference\":\"ORD-1\",\"provider_reference\":\"TKT-1\"}" | jq -r .id)
DOC=$(curl -s $B/tickets/$TICKET/documents -H "Authorization: Bearer $HOLDER" -F file=@receipt.pdf | jq -r .id)
curl -s $B/tickets/$TICKET/documents/$DOC -H "Authorization: Bearer $HOLDER"   # {"url": "...", "expires_at": "..."}
curl -s $B/admin/tickets/$TICKET/verify -H "Authorization: Bearer $ADMIN" \
  -H 'content-type: application/json' -d '{"decision":"VERIFIED","transfer_eligible":true}'
```

## Try the listing flow

Carry on from the ticket flow, with the ticket verified as **Transfer eligible**.

1. As the holder, open the ticket's page. Under **Sell this ticket**, enter an asking price no higher than what you paid and press **Submit for review** (or **Save draft** to submit later from the listing's page).
2. The listing's page shows **Pending review**. Change the price there while you wait if you like.
3. As the admin, **Review listings**: check the listing against **View the ticket and its evidence**, then **Approve**, or **Reject** with a reason the holder will see. An admin cannot review their own listing, so if your admin account is also the seller, approve it from a second admin account.
4. Back as the holder, the listing is **Active**, and **History** reads: listed as a draft, submitted for review, approved by an admin. Anyone else opening the listing's address sees the ticket and the price, but no history and no controls; a draft or pending listing is "Not found" to them.
5. **My listings** has every listing in every status. **Review listings → All listings** shows an admin everyone's.

From a terminal, carrying on with the variables above:

```bash
LISTING=$(curl -s $B/listings -H "Authorization: Bearer $HOLDER" -H 'content-type: application/json' \
  -d "{\"ticket_id\":\"$TICKET\",\"asking_price_paise\":200000}" | jq -r .id)     # a DRAFT
curl -s -X POST $B/listings/$LISTING/submit -H "Authorization: Bearer $HOLDER"       # PENDING_VERIFICATION
curl -s -X POST $B/admin/listings/$LISTING/approve -H "Authorization: Bearer $ADMIN" # ACTIVE
curl -s $B/listings/$LISTING                                                         # the public view, no token
curl -s $B/listings/$LISTING/history -H "Authorization: Bearer $HOLDER" | jq '.items[].action'
```

`ACTIVE` listings whose end has passed become `EXPIRED` when the expiry job runs, every 5 minutes in Docker. To run it by hand:

```bash
uv run python -m scripts.expire_listings   # Expired 0 listing(s).
```

## Try the search

http://localhost:5173 is the search page, with or without an account. To see it full, seed a separate database with 2,000 events and 100,000 listings (Faker; it refuses any database whose name does not end in `_perf`) and run the API against it:

```bash
export DATABASE_URL=postgresql+psycopg://ticketloop:ticketloop@localhost:5432/ticketloop_perf
uv run python -m scripts.seed_discovery --reset
uv run python -m scripts.explain_discovery   # every query's plan, page 1 against page 500; writes docs/discovery-performance.md
uv run uvicorn app.main:app
unset DATABASE_URL                           # back to the development database
```

(In PowerShell: `$env:DATABASE_URL = '…'`, and `Remove-Item Env:DATABASE_URL` after.)

```bash
curl -s "$B/listings?q=cup&city=Mumbai&max_price_paise=500000&sort=-created_at" | jq '.items[].event.name'
```

## Try the reservation

Open a listing from the search in two browsers with two accounts (neither the seller's) and press **Reserve** in both. One gets a page counting down from 10:00; the other is told somebody was first. **Cancel hold** gives the ticket back; **My holds** lists every hold.

```bash
curl -s -X POST $B/listings/$LISTING/reservations -H "Authorization: Bearer $BUYER"   # 201, or 200 if you hold it
curl -s $B/reservations/me -H "Authorization: Bearer $BUYER" | jq '.items[0]'
```

Holds that run out are closed every 30 seconds by Celery beat in Docker, or by hand:

```bash
uv run python -m scripts.expire_reservations          # every 30 seconds; --once for a single pass
```

## Try paying for it

Carry on from a hold. **Checkout** takes the buyer to fakepay's own page, where you choose what the payment does:

- **Pay**: back on the order's page, it turns to **Paid** as soon as the webhook lands, then to **Completed** once the worker has handed the ticket over. It is under the buyer's **My tickets** now, and the seller's listing says **Sold**.
- **Pay, webhook late**: the order's page says it is waiting, and turns to Paid by itself.
- **Pay, webhook twice** and **Pay, forged webhook**: the first still pays once; the second is refused with a 401, and reconciliation finds the payment instead.
- **Pay, no webhook**: the money moved and nobody told us. Run reconciliation (below) five minutes later.
- **Decline**: the order fails and the ticket is back on sale.

Close the tab halfway and come back through **My orders**: it is the same payment, never a second one. From a terminal, `Idempotency-Key` is whatever you like, the same one on a retry:

```bash
HOLD=$(curl -s -X POST $B/listings/$LISTING/reservations -H "Authorization: Bearer $BUYER" | jq -r .id)
ORDER=$(curl -s $B/orders -H "Authorization: Bearer $BUYER" -H 'Idempotency-Key: try-1' \
  -H 'content-type: application/json' -d "{\"reservation_id\":\"$HOLD\"}" | jq -r .id)
curl -s $B/payments/intents -H "Authorization: Bearer $BUYER" -H 'Idempotency-Key: try-2' \
  -H 'content-type: application/json' -d "{\"order_id\":\"$ORDER\"}" | jq -r .checkout_url   # open it, or:
# curl -s localhost:8090/v1/intents/<transaction_id>/outcome -H 'content-type: application/json' -d '{"outcome":"succeed"}'
curl -s $B/orders/$ORDER -H "Authorization: Bearer $BUYER" | jq -r .status                       # PAID
```

Payments nobody told us about are found by asking the gateway, every 2 minutes in Docker, or by hand:

```bash
uv run python -m scripts.reconcile_payments           # SUCCEEDED: checked 1, fixed 1.
```

`scripts.expire_reservations` asks too, about buyers who went to pay and never came back, and never releases a ticket without an answer. Give the API and fakepay the same `FAKEPAY_WEBHOOK_SECRET` (32 bytes or more) anywhere but your laptop; staging and production refuse to start with the built-in one.

The races themselves, against four API processes and a database of their own:

```bash
uv run pytest tests/concurrency                                              # every race, 3 rounds
RACE_ROUNDS=20 uv run pytest tests/concurrency/test_reservation_race.py      # the recorded proof
uv run pytest tests/concurrency/test_payment_race.py                         # orders, webhooks, the clock
uv run python -m tests.concurrency.report                                    # three locking strategies, compared
uv run pytest tests/concurrency/test_messaging.py                            # the M9 drills; needs RabbitMQ
```

## Watch the ticket change hands

With the worker, beat and relay running, every paid order goes on by itself: the relay publishes `payment.succeeded`, a worker creates the transfer, another asks the pretend ticket provider, and the ticket becomes the buyer's. If a message is lost on the way (it ended in a dead-letter queue), beat's `resume_transfers` job picks the order up again after 10 minutes. The worker, beat and relay restart by themselves if they exit. RabbitMQ's console (http://localhost:15672) shows the queues `transfers`, `notifications` and `maintenance`, and their dead-letter queues.

```bash
curl -s $B/orders/$ORDER -H "Authorization: Bearer $BUYER" | jq '{status, transfer: .transfer.status}'
curl -s $B/notifications -H "Authorization: Bearer $BUYER" | jq '.items[].notification_type'
docker compose logs -f worker relay                                      # what they did, as JSON
```

The drills, by hand:

- **The broker is down:** `docker compose stop rabbitmq`, pay for a few orders, and `outbox_events` fills up with unpublished rows. `docker compose start rabbitmq`: the relay publishes them all, and each ticket moves once.
- **A worker dies mid-transfer:** set `TICKET_PROVIDER_DELAY_SECONDS: "30"` on the worker in `docker-compose.yml`, pay, `docker compose kill worker` while it waits, then `docker compose up -d worker`. The message comes back, the provider is asked again with the same key, and the ticket moves once.
- **The provider fails:** `TICKET_PROVIDER_MODE` set to `retryable` (with `TICKET_PROVIDER_FAILURES`), `permanent` or `unknown`. Busy is retried 5 times, then the transfer and the order go to review; the other two go there at once.

(In PowerShell: `$env:RACE_ROUNDS = '20'` first, and `Remove-Item Env:RACE_ROUNDS` after.)

## Watch the cache and the limits

Search (`GET /listings` and `GET /events/{id}/listings`, first page) and the event page (`GET /events/{id}`) are cached in Redis for 30 to 60 seconds. Every answer says how it was served in `X-Cache`: `MISS` (computed, then stored), `HIT` (from Redis), or `BYPASS` (not cached: a later page, caching off, or Redis down). Any committed change to a listing or an event, from the API or the worker, makes the cached pages that depend on it unreachable at once (a change made by one of the hand-run scripts in `scripts/` shows within a minute), so a reservation, a review or an edit is never hidden behind a stale page.

```bash
curl -si "$B/listings?q=coldplay" | grep -i x-cache     # MISS
curl -si "$B/listings?q=coldplay" | grep -i x-cache     # HIT
docker compose exec redis redis-cli --scan --pattern 'cache:*'   # the keys and the versions
```

The rate limits are token buckets in Redis. Over the limit, the API answers `429` with `Retry-After` and the problem type `rate-limited`, and the form shows a countdown.

| Route | Limit (a minute) | Counted per | Setting |
| --- | --- | --- | --- |
| `POST /auth/login`, `POST /auth/password-reset/request` | 5 | email and address, each route its own bucket | `RATE_LIMIT_LOGIN_PER_MINUTE` |
| `POST /listings/{id}/reservations` | 10 | user | `RATE_LIMIT_RESERVE_PER_MINUTE` |
| `POST /payments/intents` | 10 | user | `RATE_LIMIT_PAYMENT_PER_MINUTE` |
| `GET /listings`, `GET /events/{id}/listings` | 60 | address | `RATE_LIMIT_SEARCH_PER_MINUTE` |

```bash
for i in 1 2 3 4 5 6; do curl -s -o /dev/null -w '%{http_code} ' $B/auth/login \
  -H 'content-type: application/json' -d '{"email":"a@b.co","password":"wrong"}'; done
# 401 401 401 401 401 429
```

**Redis is down:** `docker compose stop redis`. Every route still answers, from Postgres, with `X-Cache: BYPASS` and no limits; the API logs `redis.unavailable` once, stops asking Redis, and pings it in the background every 5 seconds until it answers (`redis.available`). Only the request that found it gone waited: in Docker, about 4 seconds, while the stopped container's name fails to resolve. `docker compose start redis` and caching and limits come back by themselves. Redis never decides anything that matters: who holds a ticket, what was paid and who owns it are all Postgres.

`CACHE_ENABLED=false` or `RATE_LIMIT_ENABLED=false` turns either off; `REDIS_URL` says where Redis is.

## Watch it run: metrics, traces and logs

| What | Where |
| --- | --- |
| Grafana: one dashboard with requests, errors and latency per route, reservations, payments, the outbox, the queues, the worker, the cache and the limits | http://localhost:3000 (no login to look; `admin` / `ticketloop` to edit) |
| Prometheus: every series, and whether each target is up | http://localhost:9090 (Status, then Target health) |
| Jaeger: traces | http://localhost:16686 |
| The raw numbers | http://localhost:8000/metrics for the API; the worker's (:9100) and the relay's (:9101) inside the compose network |

Every label takes its value from a short fixed list: a route's template (`/api/v1/listings/{listing_id}`), never the path asked for; a problem code from the known ones, else `other`; a webhook's event type, else `unknown`. Made-up paths and forged webhooks add no new series. Prometheus scrapes the API on the host's port 8000, so it finds it whether it runs in Docker or on Windows.

Tracing is on in Compose (`TRACING_ENABLED=true`, `OTLP_ENDPOINT=http://jaeger:4318`). For an API run on Windows, put `TRACING_ENABLED=true` in `.env`; it sends to Jaeger's published port on localhost. With Jaeger stopped nothing waits: spans are sent in batches from a background thread, and dropped.

**From a failed purchase to its failing step:**

```bash
TICKET_PROVIDER_MODE=permanent docker compose up -d worker   # the provider refuses every transfer
# buy a ticket as in "Try paying for it"; the order ends TRANSFER_FAILED
docker compose logs --no-log-prefix worker | grep $ORDER | jq -c '{event, reason, correlation_id}'
# {"event":"transfer.needs_review","reason":"refused: the provider refused: ticket not transferable",
#  "correlation_id":"08403b20745a7b0161d8edac5d403d85"}
# open http://localhost:16686/trace/08403b20745a7b0161d8edac5d403d85: the webhook, the relay's
# publishes and the worker's tasks in one trace, and the span "ticket provider transfer" marked
# as an error, with the exception on it
docker compose up -d worker                                  # back to succeeding
```

(In PowerShell: `$env:TICKET_PROVIDER_MODE = 'permanent'` before the first line, and `Remove-Item Env:TICKET_PROVIDER_MODE` before the last.) Jaeger finds the same trace by the order too: Search, service `ticketloop-worker`, tags `ticketloop.order_id=<the order id>`.

`/metrics` is outside `/api/v1` and needs no token, like the health check. Deployed, it belongs behind the network edge, where Prometheus can reach it and the public cannot.
