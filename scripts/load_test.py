"""The M12 load tests, each one command, each followed by the invariants check.

    docker compose up -d db
    uv run python -m scripts.load_test seed             # recreate ticketloop_perf and fill it
    uv run python -m scripts.load_test up               # the load stack (docker-compose.load.yml)
    uv run python -m scripts.load_test spike            # 1,000 buyers, one listing, at once
    uv run python -m scripts.load_test load --vus 100   # then 500 and 1000
    uv run python -m scripts.load_test load --vus 500 --apis 8   # eight API containers, once
    uv run python -m scripts.load_test limits           # one minute with the real rate limits
    uv run python -m scripts.load_test drill-broker     # RabbitMQ stopped for a minute under load
    uv run python -m scripts.load_test invariants       # check_invariants.sql on its own

k6 runs in its container on the compose network and writes each run's summary to
tests/load/results/<name>.json. A run fails (exit 1) when k6 crossed a threshold or the
invariants found a row. docs/test-plan.md says what each run expects, docs/test-results.md what
it got. `docker compose up -d`, without the load file, puts the stack back on the development
database afterwards.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

import httpx2 as httpx
from sqlalchemy import Connection, Engine, text

from app.core.config import get_settings
from app.infrastructure.database.engine import create_db_engine
from scripts.databases import database_url_with_name

ROOT = Path(__file__).resolve().parents[1]
INVARIANTS = ROOT / "scripts" / "check_invariants.sql"
RESULTS = ROOT / "tests" / "load" / "results"
COMPOSE_FILES = (ROOT / "docker-compose.yml", ROOT / "docker-compose.load.yml")
PERF_DATABASE = "ticketloop_perf"
APIS = 4  # docker-compose.load.yml's replicas; `load --apis N` changes it for one run
# The limits the API ships with (config.py), for the one run that keeps them.
REAL_LIMITS = {
    "LOAD_LOGIN_PER_MINUTE": "5",
    "LOAD_RESERVE_PER_MINUTE": "10",
    "LOAD_PAYMENT_PER_MINUTE": "10",
    "LOAD_SEARCH_PER_MINUTE": "60",
}
K6_THRESHOLDS_CROSSED = 99  # k6's exit code when the run finished but a threshold failed
# Besides the tagged ones (http_req_duration{kind:read} and the like), the timings worth printing.
TRENDS = ("http_req_duration", "time_to_paid")


class Violation(NamedTuple):
    invariant: str
    subject: str
    detail: str


def violations(bind: Engine | Connection) -> list[Violation]:
    """Every row check_invariants.sql returns: none means every invariant holds."""
    query = text(INVARIANTS.read_text("utf-8"))
    if isinstance(bind, Engine):
        with bind.connect() as connection:
            return [Violation(*row) for row in connection.execute(query)]
    return [Violation(*row) for row in bind.execute(query)]


# ---------------------------------------------------------------- commands


def compose(*args: str) -> list[str]:
    files = [word for path in COMPOSE_FILES for word in ("-f", str(path))]
    return ["docker", "compose", *files, *args]


def api_ports(apis: int) -> str:
    """The host ports for that many API containers, one each from 8000."""
    return f"8000-{8000 + apis - 1}"


def api_urls(apis: int) -> list[str]:
    return [f"http://localhost:{8000 + i}/api/v1/health" for i in range(apis)]


def compose_env(extra: Mapping[str, str] | None = None, apis: int = APIS) -> dict[str, str]:
    """This environment, with the API containers' ports and any limits to change."""
    return {**os.environ, "API_PORTS": api_ports(apis), **(extra or {})}


def scale_command(apis: int) -> list[str]:
    return compose("up", "-d", "--no-deps", "--scale", f"api={apis}", "api")


def run_name(vus: int, apis: int) -> str:
    return f"load-{vus}" if apis == APIS else f"load-{vus}-{apis}-apis"


def k6_command(script: str, name: str, env: Mapping[str, str] | None = None) -> list[str]:
    """k6 in its container. --no-deps: starting it must never recreate the API containers."""
    variables = [word for key, value in {"NAME": name, **(env or {})}.items()
                 for word in ("-e", f"{key}={value}")]  # fmt: skip
    return compose("run", "--rm", "--no-deps", "k6", "run", "--quiet", *variables,
                   f"/scripts/{script}.js")  # fmt: skip


def failed_thresholds(summary: Mapping[str, Any]) -> list[str]:
    """`metric expression` for every threshold k6 reports as not met."""
    return [
        f"{metric} {expression}"
        for metric, values in summary["metrics"].items()
        for expression, result in values.get("thresholds", {}).items()
        if not result["ok"]
    ]


def digest(summary: Mapping[str, Any]) -> list[str]:
    """The numbers worth reading from a k6 summary, one per line."""
    metrics = summary["metrics"]
    requests = metrics.get("http_reqs", {}).get("values", {})
    lines = [f"requests        {requests.get('count', 0):,.0f} ({requests.get('rate', 0):,.1f}/s)"]
    failed = metrics.get("http_req_failed", {}).get("values", {})
    if failed:
        lines.append(f"failed          {failed.get('rate', 0):.2%}")
    for name, metric in sorted(metrics.items()):
        values = metric.get("values", {})
        if metric.get("type") == "trend" and ("{" in name or name in TRENDS):
            lines.append(
                f"{name:<40} p50 {values.get('med', 0):7.1f}  p95 {values.get('p(95)', 0):7.1f}"
                f"  p99 {values.get('p(99)', 0):7.1f}  max {values.get('max', 0):7.1f} ms"
            )
        elif metric.get("type") == "counter" and not name.startswith(("http_", "data_")):
            lines.append(f"{name:<40} {values.get('count', 0):,.0f}")
    return lines


MEBIBYTES = {"B": 1 / 2**20, "KiB": 1 / 1024, "MiB": 1.0, "GiB": 1024.0}


def service_of(container: str) -> str:
    """ticketloop-api-3 is api; k6's one-off ticketloop-k6-run-<id> is k6."""
    name = container.removeprefix("ticketloop-")
    if name.startswith("k6-run"):
        return "k6"
    base, _, number = name.rpartition("-")
    return base if number.isdigit() else name


def mebibytes(usage: str) -> float:
    """The used half of docker's "123.4MiB / 11.54GiB"."""
    used = usage.split("/")[0].strip()
    for unit in sorted(MEBIBYTES, key=len, reverse=True):
        if used.endswith(unit):
            return float(used.removesuffix(unit)) * MEBIBYTES[unit]
    return 0.0


def peaks(samples: Sequence[Sequence[Mapping[str, str]]]) -> dict[str, dict[str, float]]:
    """Per service, the highest CPU and memory seen in any one sample, with a service's
    containers (the four API ones) added up first."""
    found: dict[str, dict[str, float]] = {}
    for sample in samples:
        totals: dict[str, dict[str, float]] = {}
        for line in sample:
            service = totals.setdefault(
                service_of(line["Name"]), {"cpu_percent": 0.0, "memory_mib": 0.0}
            )
            service["cpu_percent"] += float(line["CPUPerc"].rstrip("%") or 0)
            service["memory_mib"] += mebibytes(line["MemUsage"])
        for name, total in totals.items():
            peak = found.setdefault(name, {"cpu_percent": 0.0, "memory_mib": 0.0})
            for key, value in total.items():
                peak[key] = max(peak[key], round(value, 1))
    return found


class Sampler(threading.Thread):
    """`docker stats` every few seconds while a run goes on."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.samples: list[list[dict[str, str]]] = []
        self.done = threading.Event()

    def run(self) -> None:
        command = ["docker", "stats", "--no-stream", "--format", "{{json .}}"]
        while not self.done.is_set():
            listed = subprocess.run(command, capture_output=True, text=True, check=False)
            lines = [json.loads(line) for line in listed.stdout.splitlines() if line.strip()]
            if lines:
                self.samples.append(lines)
            self.done.wait(3)

    def stop(self, name: str, started: float, ended: float) -> None:
        """Peaks and Prometheus's numbers for the run, into <name>-stats.json."""
        self.done.set()
        self.join(timeout=30)
        time.sleep(max(0.0, ended - time.time()))
        found = peaks(self.samples)
        dashboard = from_prometheus(started, ended)
        stats = {
            "started": started,
            "ended": ended,
            "samples": len(self.samples),
            "peaks": found,
            "prometheus": dashboard,
        }
        (RESULTS / f"{name}-stats.json").write_text(json.dumps(stats, indent=1), "utf-8")
        for key, value in dashboard.items():
            print(f"  {key:<30} {value}")
        busiest = sorted(found.items(), key=lambda item: -item[1]["cpu_percent"])[:6]
        print("  peak CPU, MiB   " + ", ".join(
            f"{service} {peak['cpu_percent']:.0f}% {peak['memory_mib']:.0f}"
            for service, peak in busiest
        ))  # fmt: skip


LIVE_QUEUES = '{queue=~"(.+-)?(transfers|notifications|maintenance)"}'


def prometheus_queries(seconds: int) -> dict[str, str]:
    """What the dashboard showed over one run, as numbers: the run's last `seconds`."""
    window = f"[{seconds}s]"
    over = f"[{seconds}s:5s]"
    return {
        "cache hit ratio": f'sum(increase(cache_requests_total{{outcome="hit"}}{window}))'
        f" / sum(increase(cache_requests_total{window}))",
        "connections in use, most": f"max_over_time(sum(db_pool_checked_out_connections){over})",
        "outbox backlog, most": f"max_over_time(max(outbox_backlog){over})",
        "oldest unpublished event, s": "max_over_time(max(outbox_oldest_unpublished_seconds)"
        f"{over})",
        "messages waiting, most": "max_over_time(sum(rabbitmq_queue_messages_ready"
        f"{LIVE_QUEUES}){over})",
    }


def from_prometheus(started: float, ended: float) -> dict[str, float | None]:
    """Each of prometheus_queries() at the end of the run, from Prometheus on :9090."""
    found: dict[str, float | None] = {}
    for name, query in prometheus_queries(max(10, round(ended - started))).items():
        try:
            reply = httpx.get(
                "http://localhost:9090/api/v1/query",
                params={"query": query, "time": f"{ended:.0f}"},
                timeout=10,
            ).json()
            result = reply["data"]["result"]
            found[name] = round(float(result[0]["value"][1]), 3) if result else None
        except (httpx.HTTPError, KeyError, ValueError):
            found[name] = None
    return found


# ---------------------------------------------------------------- running them


def perf_engine() -> Engine:
    settings = get_settings()
    url = database_url_with_name(settings.database_url, PERF_DATABASE)
    return create_db_engine(settings.model_copy(update={"database_url": url}))


def sh(command: Sequence[str], env: Mapping[str, str] | None = None, apis: int = APIS) -> None:
    print(
        "$", " ".join(command[-6:]) if command[:2] == ["docker", "compose"] else " ".join(command)
    )
    subprocess.run(command, env=compose_env(env, apis), cwd=ROOT, check=True)


def answers(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def wait_for_api(seconds: float = 180, apis: int = APIS) -> None:
    """Until every API container answers its health check."""
    deadline = time.monotonic() + seconds
    waiting = api_urls(apis)
    while waiting:
        waiting = [url for url in waiting if not answers(url)]
        if waiting and time.monotonic() > deadline:
            raise SystemExit(f"API not up after {seconds:.0f} s: {waiting}")
        if waiting:
            time.sleep(1)


def run_k6(script: str, name: str, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    sampler = Sampler()
    sampler.start()
    started = time.time()
    result = subprocess.run(k6_command(script, name, env), env=compose_env(), cwd=ROOT)
    sampler.stop(name, started, time.time() + 6)  # one more scrape after the end
    if result.returncode not in (0, K6_THRESHOLDS_CROSSED):
        raise SystemExit(f"k6 failed to run {script}.js (exit {result.returncode})")
    summary: dict[str, Any] = json.loads((RESULTS / f"{name}.json").read_text("utf-8"))
    print(f"\n{name}")
    for line in digest(summary):
        print(f"  {line}")
    return summary


def orders_by_status(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT status, count(*) FROM orders GROUP BY status"))
        return {status: count for status, count in rows}


def verdict(engine: Engine, summary: Mapping[str, Any] | None, *problems: str) -> int:
    """Print what failed, if anything, and the exit code: the thresholds, then the invariants."""
    failed = [*(failed_thresholds(summary) if summary else []), *problems]
    found = violations(engine)
    print(f"  orders          {orders_by_status(engine)}")
    for crossed in failed:
        print(f"  FAILED          {crossed}")
    for row in found:
        print(f"  VIOLATION       {row.invariant} {row.subject}: {row.detail}")
    print(f"  invariants      {len(found)} rows")
    return 1 if failed or found else 0


def seed() -> int:
    settings = get_settings()
    url = database_url_with_name(settings.database_url, PERF_DATABASE)
    command = [sys.executable, "-m", "scripts.seed_discovery", "--reset"]
    subprocess.run(command, env={**os.environ, "DATABASE_URL": url}, cwd=ROOT, check=True)
    return 0


def up() -> int:
    sh(compose("up", "-d", "--build"))
    wait_for_api()
    print("Four API containers up; Grafana at http://localhost:3000")
    return 0


def spike() -> int:
    engine = perf_engine()
    summary = run_k6("spike", "spike")
    listing = summary["setup_data"]["listing_id"]
    with engine.connect() as connection:
        live = connection.execute(
            text(
                "SELECT count(*) FROM reservations WHERE listing_id = :listing"
                " AND status IN ('CREATED', 'PAYMENT_PENDING', 'CONFIRMED')"
            ),
            {"listing": listing},
        ).scalar_one()
    print(f"  live holds      {live} on listing {listing}")
    return verdict(engine, summary, *([] if live == 1 else [f"{live} live reservations"]))


def load(vus: int, hold: str, apis: int = APIS) -> int:
    """With `apis` other than four, the API is scaled for this run alone, then put back."""
    if apis == APIS:
        summary = run_k6("load", run_name(vus, apis), {"VUS": str(vus), "HOLD": hold})
        return verdict(perf_engine(), summary)
    try:
        sh(scale_command(apis), apis=apis)
        wait_for_api(apis=apis)
        summary = run_k6("load", run_name(vus, apis), {"VUS": str(vus), "HOLD": hold})
    finally:
        sh(scale_command(APIS))
        wait_for_api()
    return verdict(perf_engine(), summary)


def limits() -> int:
    """With the limits the API ships with, then the raised ones back, whatever happened."""
    try:
        sh(compose("up", "-d", "--no-deps", "api"), env=REAL_LIMITS)
        wait_for_api()
        summary = run_k6("limits", "limits")
    finally:
        sh(compose("up", "-d", "--no-deps", "api"))
        wait_for_api()
    return verdict(perf_engine(), summary)


def unsettled(engine: Engine) -> tuple[int, int]:
    """(events not yet published, orders paid but not yet completed or failed)."""
    with engine.connect() as connection:
        backlog = connection.execute(
            text("SELECT count(*) FROM outbox_events WHERE published_at IS NULL")
        ).scalar_one()
        paid = connection.execute(
            text("SELECT count(*) FROM orders WHERE status IN ('PAID', 'TRANSFER_PENDING')")
        ).scalar_one()
    return backlog, paid


def drill_broker() -> int:
    """100 users for 4 minutes; RabbitMQ down from 60 s to 120 s; then everything settles."""
    engine = perf_engine()
    sampler = Sampler()
    sampler.start()
    began = time.time()
    k6 = subprocess.Popen(
        k6_command("load", "drill-broker", {"VUS": "100", "HOLD": "4m"}),
        env=compose_env(),
        cwd=ROOT,
    )
    time.sleep(60)
    sh(compose("stop", "rabbitmq"))
    stopped = time.monotonic()
    time.sleep(60)
    down_backlog, down_paid = unsettled(engine)
    print(f"  broker down {time.monotonic() - stopped:.0f} s: backlog {down_backlog}, "
          f"paid and waiting {down_paid}")  # fmt: skip
    sh(compose("start", "rabbitmq"))
    started = time.monotonic()
    if k6.wait() not in (0, K6_THRESHOLDS_CROSSED):
        raise SystemExit("k6 failed to run the drill")
    sampler.stop("drill-broker", began, time.time() + 6)
    summary: dict[str, Any] = json.loads((RESULTS / "drill-broker.json").read_text("utf-8"))
    for line in digest(summary):
        print(f"  {line}")
    deadline = time.monotonic() + 300
    while (left := unsettled(engine)) != (0, 0) and time.monotonic() < deadline:
        time.sleep(5)
    print(f"  settled         {time.monotonic() - started:.0f} s after the broker came back; "
          f"left {left}")  # fmt: skip
    problems = [] if down_backlog > 0 else ["the backlog never grew with the broker down"]
    if left != (0, 0):
        problems.append(f"not settled after 5 minutes: {left}")
    return verdict(engine, summary, *problems)


def invariants() -> int:
    return verdict(perf_engine(), None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="recreate ticketloop_perf and fill it")
    commands.add_parser("up", help="start the load stack and wait for the four API containers")
    commands.add_parser("spike", help="1,000 buyers reserve one listing at once")
    loading = commands.add_parser("load", help="80%% browse, 20%% buy")
    loading.add_argument("--vus", type=int, default=100)
    loading.add_argument("--hold", default="3m", help="how long to hold the users, as k6 says it")
    loading.add_argument(
        "--apis", type=int, default=APIS, help="API containers for this run, then back to four"
    )
    commands.add_parser("limits", help="one minute of search with the real rate limits")
    commands.add_parser("drill-broker", help="RabbitMQ stopped for a minute under 100 users")
    commands.add_parser("invariants", help="check_invariants.sql on the perf database")
    args = parser.parse_args(argv)
    match args.command:
        case "seed":
            return seed()
        case "up":
            return up()
        case "spike":
            return spike()
        case "load":
            return load(args.vus, args.hold, args.apis)
        case "limits":
            return limits()
        case "drill-broker":
            return drill_broker()
        case _:
            return invariants()


if __name__ == "__main__":
    sys.exit(main())
