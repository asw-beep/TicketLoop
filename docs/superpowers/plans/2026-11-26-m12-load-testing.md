# M12 · Load testing and results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measured throughput and p50/p95/p99 for the finished system, 1,000 users on one listing giving one reservation and no 5xx, zero oversells in every run, and a README that starts it in one command and reports the numbers.

**Architecture:** A compose override (`docker-compose.load.yml`) runs the same images on the seeded `ticketloop_perf` database with four API containers, each its own Prometheus target found by DNS, and the rate limits raised. k6 runs from its Docker image inside the compose network. `scripts/load_test.py` wraps every run so the invariants SQL runs after each one. A `web` container (nginx serving the built frontend, `/api` proxied) makes `docker compose up -d --build` the whole demo.

**Tech Stack:** k6 1.4.2 (`grafana/k6:1.4.2`), Docker Compose, Postgres 17, nginx 1.29.3, Node 24.11.1 for the frontend build, pytest.

**Spec:** `docs/superpowers/specs/2026-11-23-slim-roadmap-m6-m12-design.md`, section "M12 · Load testing and results"; NFR-02 and NFR-03 in `docs/requirements.md`.

## Global Constraints

- Versions pinned exactly: `grafana/k6:1.4.2`, `nginx:1.29.3-alpine`, `node:24.11.1-alpine3.22`.
- NFR-03 targets: normal reads under 300 ms p95; reservation under 500 ms p95; errors under 1%. Reported with the hardware, as findings, not promises.
- Load stages 100, 500 and 1,000 virtual users, 80% browse (search, view), 20% buy (reserve, order, pay with fakepay set to succeed).
- Spike: 0 to 1,000 users in seconds on one listing; exactly one reservation, every other buyer a clean 409, no 5xx.
- Races at scale: the M7 race 100 times on 4 workers, and M8's expiry-versus-payment race.
- Broker drill under load: RabbitMQ stopped mid-run; the backlog grows, drains after restart, every paid order completes once.
- The load runs raise the rate limits and say so; one short run with the real limits shows the 429s.
- `scripts/check_invariants.sql` runs after every test and returns zero rows: no ticket in two open listings, no listing with two live reservations, every succeeded payment's order PAID or later, every completed order's ticket owned by its buyer.
- `docs/test-plan.md` is written before anything runs: hypothesis, script, metric and pass line per test.
- No screens. Keep at minimum, if the day runs short: the spike, the races at scale, the 100-user stage, the invariants and the README Results.
- Commit `M12: Load testing and results`, no co-author or tool attribution anywhere.

## Review Focus

1. **The base stack unchanged:** `docker compose up` without the override still publishes the API on 8000 alone, with fakepay's webhook to the host and the real rate limits. A load-only setting leaking into the base file would ship raised limits.
2. **A k6 check that cannot fail:** a reservation script that counts 409 as success, or thresholds on a metric that is never emitted, passes every run. Every threshold names a metric the script emits, and the spike counts 201s, 409s and everything else apart.
3. **Invariants under resale:** a ticket bought, then listed again and bought by someone else, is legitimate; only the latest completed order for a ticket says who owns it.
4. **The web container with the API down or a deep link:** nginx must start before the API answers (resolve `api` per request), and a refresh on `/orders/<id>` must serve the app, not a 404.
5. **A run that fails halfway:** the runner must not leave the API on the real limits (or the raised ones) silently; the limits run restores the raised limits in a `finally`.

---

### Task 1: The test plan and the load stack

**Files:**
- Create: `docs/test-plan.md`, `docker-compose.load.yml`, `observability/prometheus-load.yml`
- Modify: `docker-compose.yml` (api ports `"${API_PORTS:-8000}:8000"`), the dashboard generator and `observability/grafana/dashboards/ticketloop.json` (pool panel `sum(...)`, titled for every API process)
- Test: `tests/unit/test_compose.py`, `tests/unit/test_observability_config.py`

**Interfaces:**
- Produces: the override's service settings below; the k6 service `k6` (profile `load`) with `/scripts` (ro, `tests/load`) and `/results` (`tests/load/results`) mounts and `BASE_URL=http://api:8000`, `FAKEPAY_URL=http://fakepay:8090`; `API_PORTS=8000-8003` is what every load command sets.

- [ ] **Step 1: Write the failing tests.** In `test_compose.py`, load `docker-compose.load.yml` with `yaml.safe_load` (so no `!reset`/`!override` tags) and assert:
  - every app service (api, worker, beat, relay, migrate) has `DATABASE_URL` ending `/ticketloop_perf`;
  - api `deploy.replicas == 4`;
  - the four `RATE_LIMIT_*_PER_MINUTE` values are `${LOAD_*_PER_MINUTE:-1000000}`;
  - `ACCESS_TOKEN_MINUTES` is set;
  - `OTEL_TRACES_SAMPLER == "parentbased_traceidratio"` with arg `"0.01"` on api, worker and relay;
  - fakepay's webhook URL is `http://api:8000/api/v1/payments/webhooks/fakepay`;
  - the worker runs `--concurrency 8`;
  - db runs `max_connections=200`;
  - prometheus mounts `./observability/prometheus-load.yml` at the same target;
  - the k6 image is `grafana/k6:1.4.2` with profile `load`.

  Base file: api ports `["${API_PORTS:-8000}:8000"]`, and no `RATE_LIMIT_` or `OTEL_TRACES_SAMPLER` key in any base service.

  In `test_observability_config.py`: prometheus-load has the same jobs as prometheus.yml, except that `api` uses `dns_sd_configs` `[{"names": ["api"], "type": "A", "port": 8000, ...}]`. The dashboard's pool panel expression is `sum(db_pool_checked_out_connections)`.
- [ ] **Step 2: Run them.** Run `uv run pytest tests/unit/test_compose.py tests/unit/test_observability_config.py -q`. Expected: FAIL (no override file).
- [ ] **Step 3: Write `docker-compose.load.yml`, `observability/prometheus-load.yml`, the base ports change and the dashboard change.** Add log rotation (`max-size: 20m`, `max-file: 3`) on api and worker in the override. Then write `docs/test-plan.md`: hardware, data volume and configuration; then per test (spike, load ×3, limits, broker drill, races at scale, invariants) the hypothesis, the command, the metric and the pass line from Global Constraints.
- [ ] **Step 4: Run them.** Expected: PASS. Then `API_PORTS=8000-8003 docker compose -f docker-compose.yml -f docker-compose.load.yml config --quiet` exits 0.

### Task 2: The invariants

**Files:**
- Create: `scripts/check_invariants.sql`, `tests/integration/test_invariants.py`
- Create: `scripts/load_test.py` with `violations(engine: Engine) -> list[Violation]` (`Violation = NamedTuple(invariant: str, subject: str, detail: str)`); it reads the SQL file and runs it as one statement.

**Interfaces:**
- Produces: `violations(engine)` for the runner (Task 3); the SQL returns columns `invariant, subject, detail`, one row per violation.

The five checks, as a `UNION ALL`:
1. `two-open-listings`: tickets with more than one listing in `DRAFT, PENDING_VERIFICATION, ACTIVE, RESERVED`.
2. `two-live-reservations`: listings with more than one reservation in `CREATED, PAYMENT_PENDING, CONFIRMED`.
3. `paid-order-not-paid`: payments `SUCCEEDED` whose order is not in `PAID, TRANSFER_PENDING, COMPLETED, TRANSFER_FAILED`.
4. `owner-is-not-buyer`: for each ticket, the latest `COMPLETED` order (by `updated_at`, then `id`), where `tickets.current_owner_id <> orders.buyer_id`.
5. `sold-twice`: listings with more than one order in `PAID, TRANSFER_PENDING, COMPLETED, TRANSFER_FAILED`. This is the oversell itself.

- [ ] **Step 1: Write the failing tests** (integration, rolled-back transaction):
  - `test_a_clean_purchase_breaks_no_invariant`: a completed purchase built with the factories gives `[]`.
  - `test_each_invariant_catches_its_violation[...]`, parametrized over the five: build the violating rows. Where a constraint forbids them, `DROP INDEX`/`DROP CONSTRAINT` first, inside the same rolled-back transaction. Assert exactly one row whose `invariant` is the one under test.
  - `test_a_ticket_resold_to_someone_else_is_owned_by_the_last_buyer`: two completed orders for one ticket, the later buyer the owner, gives `[]`.
- [ ] **Step 2: Run them.** Run `uv run pytest tests/integration/test_invariants.py -q`. Expected: FAIL (`scripts.load_test` missing).
- [ ] **Step 3: Write the SQL and `violations()`.**
- [ ] **Step 4: Run them.** Expected: PASS.

### Task 3: The k6 scripts and the runner

**Files:**
- Create: `tests/load/lib.js` (`BASE`, `FAKEPAY`, `logIn(count) -> string[]`, which logs in the seeded buyers in batches of 10; `eventIds(max) -> string[]`), `tests/load/spike.js`, `tests/load/load.js`, `tests/load/limits.js`, `tests/load/results/.gitkeep`
- Modify: `scripts/load_test.py`, with subcommands `seed`, `up`, `spike`, `load --vus N [--hold 3m]`, `limits`, `drill-broker`, `invariants`; each run ends with `violations()` and exits 1 on any.
- Test: `tests/unit/test_load_test.py`

**Interfaces:**
- Consumes: `violations(engine)` (Task 2); the `k6` compose service (Task 1).
- Produces: summaries at `tests/load/results/<name>.json` via `--summary-export`, plus `<name>-setup.json` written by `handleSummary` with `setup_data` (the spike's listing id); `compose(*args) -> list[str]`, which is the `docker compose -f docker-compose.yml -f docker-compose.load.yml` prefix; `REAL_LIMITS: dict[str, str]` = `{"LOAD_LOGIN_PER_MINUTE": "5", "LOAD_RESERVE_PER_MINUTE": "10", "LOAD_PAYMENT_PER_MINUTE": "10", "LOAD_SEARCH_PER_MINUTE": "60"}`, the config defaults.

Script rules:
- **Reads** are tagged `kind:read`; reservations are tagged `name:reserve`.
- **Statuses:** the reserve call's `responseCallback` is `http.expectedStatuses({min: 200, max: 299}, 409)`. Everything else uses the default.
- **load.js:**
  - Two `ramping-vus` scenarios, browse (0.8 × VUS) and buy (0.2 × VUS): 30 s ramp, `HOLD` (default 3 m), 15 s down.
  - Browse: a city search, one listing's page and its event's page, then 1 to 2 s of think time.
  - Buy: search by a random event, then reserve (on a 409, stop). Then order, intent and fakepay `POST /v1/intents/{txn}/outcome {"outcome":"succeed"}`. Then poll the order every 0.5 s for up to 30 s until it is `PAID` or later, and record the Trend `time_to_paid`.
  - Thresholds: `http_req_duration{kind:read}: p(95)<300`, `http_req_duration{name:reserve}: p(95)<500`, `http_req_failed: rate<0.01`.
- **spike.js:**
  - `per-vu-iterations`, 1,000 VUs × 1. Setup logs in 1,000 buyers and picks the first listing of `GET /listings`.
  - Counters `reserved`, `refused` (409), `server_errors` (5xx) and `unexpected` (anything else).
  - Thresholds: `reserved: count==1`, `refused: count==999`, `server_errors: count==0`, `unexpected: count==0`.
  - The runner also checks the database: exactly one live reservation for that listing.
- **limits.js:**
  - 20 VUs for 1 m, each searching without pause from one address.
  - Counters `limited` (429 with `Retry-After`) and `server_errors`.
  - Thresholds: `limited: count>0`, `server_errors: count==0`.
- **drill-broker:**
  - Starts `load --vus 100 --hold 4m` in the background. At +60 s it stops RabbitMQ, and at +120 s it starts it again.
  - After k6 ends, it waits (up to 5 m) for `outbox_events` with `published_at IS NULL` to reach 0 and for no order to be left in `PAID` or `TRANSFER_PENDING`.
  - Then it runs the invariants and prints the counts.
- **limits:** recreates the api containers with `REAL_LIMITS`, runs `limits.js`, and in a `finally` recreates them with the raised limits.

- [ ] **Step 1: Write the failing tests** for the pure parts:
  - `compose()` names both files and passes `API_PORTS=8000-8003` in the env it builds (`compose_env(extra) -> dict`).
  - `REAL_LIMITS` equals `Settings()`'s defaults for the four limits.
  - `k6_command("spike")` mounts nothing itself (the service does) and passes `--summary-export /results/spike.json`.
  - Parsing a k6 summary file gives the thresholds that failed (`failed_thresholds(summary: dict) -> list[str]`).
- [ ] **Step 2: Run them.** Run `uv run pytest tests/unit/test_load_test.py -q`. Expected: FAIL.
- [ ] **Step 3: Write the scripts and the runner.**
- [ ] **Step 4: Run them.** Expected: PASS. Then `docker compose ... run --rm k6 inspect /scripts/<each>.js` exits 0 for all three.

### Task 4: The runs and the results

**Files:**
- Create: `tests/load/results/*.json` (the recorded summaries), `docs/test-results.md`, `documentation/assets/m12-*.png`

- [ ] **Step 1: Seed and start.** Run `docker compose up -d db`, then `uv run python -m scripts.load_test seed`, then `uv run python -m scripts.load_test up`. Expected: 2,000 events, 100,000 listings, 1,000 buyers; four api targets up in Prometheus.
- [ ] **Step 2: Spike.** Run `uv run python -m scripts.load_test spike`. Expected: 1 reserved, 999 refused, 0 5xx; invariants 0 rows.
- [ ] **Step 3: Load 100, 500, 1,000.** Expected: thresholds reported, pass or explained; invariants 0 rows after each; a Grafana screenshot per stage.
- [ ] **Step 4: Real limits.** Expected: 429s counted, no 5xx; the raised limits restored.
- [ ] **Step 5: Broker drill.** Expected: the backlog rises while RabbitMQ is down and drains after; no order left in PAID or TRANSFER_PENDING; invariants 0 rows; a screenshot of the backlog panel.
- [ ] **Step 6: Races at scale.** Run `RACE_ROUNDS=100 uv run pytest tests/concurrency/test_reservation_race.py -q`, then `RACE_PAID_ROUNDS=100 uv run pytest tests/concurrency/test_payment_race.py -q -k expir`. Expected: all pass; record the durations.
- [ ] **Step 7: Write `docs/test-results.md`.** It holds the hardware, data volume and configuration, then a table per test (requests per second, p50/p95/p99 per kind, errors, the database connections, CPU and memory from `docker stats` at peak, the cache hit ratio and queue delay from Prometheus), the screenshots, and every failure explained.

### Task 5: One command, the README, the docs page

**Files:**
- Create: `frontend/Dockerfile` (node build stage, nginx stage), `frontend/nginx.conf`, `frontend/.dockerignore`, `docs/walkthrough.md`
- Modify: `docker-compose.yml` (`web` service: build `./frontend`, port `5173:80`, `depends_on: [api]` with no condition), `README.md`, `documentation/index.html`, the M11 page's pager
- Create: `documentation/m12-load-testing-and-results.html`
- Test: `tests/unit/test_compose.py`, `tests/unit/test_web_image.py`

- [ ] **Step 1: Write the failing tests.**
  - The `web` service builds from `./frontend` and publishes 5173.
  - `nginx.conf` has `resolver 127.0.0.11`, proxies `/api/` through a variable (resolved per request), and uses `try_files $uri /index.html`.
  - The Dockerfile pins `node:24.11.1-alpine3.22` and `nginx:1.29.3-alpine`.
  - `.dockerignore` names `node_modules`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Write the files.** Run `docker compose up -d --build web`, then `curl localhost:5173/orders/x` (gives the app's HTML) and `curl localhost:5173/api/v1/health` (gives the API's JSON).
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Rewrite the README.** In order:
  - one paragraph on what it is, and what it proves, with the numbers;
  - the one-command start;
  - an architecture diagram (Mermaid);
  - a 5-minute demo script (a full purchase in the browser, then the broker drill);
  - Results (the table from `docs/test-results.md`, with hardware and data volume);
  - status, run it day to day, test and lint, layout and design docs.

  The "Try the ..." and "Watch ..." sections move whole to `docs/walkthrough.md`.
- [ ] **Step 6: The docs page and index.** Run `shipdocs` for M12, then the full suite, ruff, the frontend checks, and the browser tests once against the finished stack.
