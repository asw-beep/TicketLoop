"""Run the 100-buyer race many times with each locking strategy and record what it cost.

    uv run python -m tests.concurrency.report               # 20 rounds each
    uv run python -m tests.concurrency.report --rounds 5 --no-write

Writes the results into docs/reservation-design.md, between its two `results` markers. Exits 1
if any round of any strategy did not end with exactly one winner.
"""

import argparse
import os
import platform
import statistics
import sys
from pathlib import Path

from sqlalchemy import Engine, make_url, text

from app.core.config import Settings
from app.infrastructure.database.engine import create_db_engine
from scripts.databases import database_url_with_name, recreate_database, upgrade
from tests.concurrency.harness import BUYERS, WORKERS, ApiServer, Buyer, Reply, make_buyers, race

DOC = Path(__file__).resolve().parents[2] / "docs" / "reservation-design.md"
START, END = "<!-- results:start -->", "<!-- results:end -->"
STRATEGIES = {
    "conditional_update": "Conditional `UPDATE` (the app)",
    "row_lock": "`SELECT … FOR UPDATE`, check in Python",
    "serializable": "`SERIALIZABLE`, retry on 40001",
}
WARM_UP_ROUNDS = 3


def measure(
    strategy: str, rounds: int, settings: Settings, engine: Engine, buyers: list[Buyer]
) -> list[list[Reply]]:
    """Every reply of every round. race() raises if a round did not have exactly one winner."""
    with ApiServer(
        settings, app="tests.concurrency.strategy_app:app", env={"RESERVE_STRATEGY": strategy}
    ) as server:
        for _ in range(WARM_UP_ROUNDS):
            race(server.urls, engine, buyers)
        return [race(server.urls, engine, buyers) for _ in range(rounds)]


def percentile(values: list[float], p: int) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[p - 1]


def row(name: str, rounds: list[list[Reply]]) -> str:
    replies = [reply for round_ in rounds for reply in round_]
    times = [reply.milliseconds for reply in replies]
    won = [reply for reply in replies if reply.status == 201]
    conflicts = sum(reply.status == 409 for reply in replies)
    retries = sum(reply.attempts - 1 for reply in replies)
    winner = statistics.median(reply.milliseconds for reply in won)
    return (
        f"| {name} | {len(won)} | {conflicts:,} | {retries:,} "
        f"| {statistics.median(times):.0f} ms | {percentile(times, 95):.0f} ms "
        f"| {max(times):.0f} ms | {winner:.0f} ms |"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--no-write", action="store_true", help="print only")
    args = parser.parse_args(argv)

    settings = Settings(_env_file=None, environment="test")
    name = f"{make_url(settings.database_url).database}_race_test"
    url = database_url_with_name(settings.database_url, name)
    settings = settings.model_copy(update={"database_url": url})
    recreate_database(url)
    engine = create_db_engine(settings)
    try:
        upgrade(engine, "head")
        buyers = make_buyers(engine, settings)
        rows = [
            row(title, measure(strategy, args.rounds, settings, engine, buyers))
            for strategy, title in STRATEGIES.items()
        ]
        with engine.connect() as connection:
            postgres = connection.scalar(text("SHOW server_version"))
    except AssertionError as error:
        print(f"A round did not have exactly one winner: {error}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    body = "\n".join(
        [
            START,
            "",
            "Written by `uv run python -m tests.concurrency.report`; do not edit by hand.",
            "",
            f"- **Race:** {BUYERS} buyers, one listing, released together, {args.rounds} rounds "
            f"per strategy after {WARM_UP_ROUNDS} to warm up. Every round of all three ended with "
            "exactly one winner, checked in the database.",
            f"- **Server:** {WORKERS} API processes, 15 database connections each at most.",
            f"- **Where:** {platform.system()} {platform.release()}, {platform.processor()}, "
            f"{os.cpu_count()} logical CPUs; PostgreSQL {postgres} in Docker. The buyers, the API "
            "and the database share the one machine.",
            "- **Times:** measured by the buyer, queueing included.",
            "",
            "| Strategy | Won | 409 | Retried | p50 | p95 | Slowest | Winner's median |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
            *rows,
            "",
            END,
        ]
    )
    print(body)
    if not args.no_write:
        doc = DOC.read_text(encoding="utf-8")
        before, rest = doc.split(START, 1)
        after = rest.split(END, 1)[1]
        DOC.write_bytes((before + body + after).encode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
