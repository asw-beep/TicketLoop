"""Prove the search queries use their indexes, and that page 500 costs what page 1 costs.

    DATABASE_URL=postgresql+psycopg://ticketloop:ticketloop@localhost:5432/ticketloop_perf \
        uv run python -m scripts.explain_discovery

Runs `EXPLAIN (ANALYZE)` on the exact statements the API runs, against the database
scripts/seed_discovery.py fills, and writes docs/discovery-performance.md. Each query is run
twice: as it is, and "before", with M6's indexes dropped inside a transaction that is rolled
back. Exits 1 if any query still scans the whole listings table, or reads more than 10,000 of
them for one page, or if page 500 takes more than 20% longer than page 1.
"""

import argparse
import platform
import statistics
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, Select, make_url, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.pagination import page_stmt, paginate
from app.infrastructure.database.engine import create_db_engine
from app.modules.discovery.filters import LISTING_SORTS, ListingFilters, ListingSort
from app.modules.discovery.service import search_stmt
from app.modules.events.models import EventCategory
from app.modules.events.service import SOONEST_FIRST, EventFilters, published_events_stmt
from app.modules.listings.models import Listing
from scripts.databases import is_throwaway

REPORT = Path(__file__).resolve().parents[1] / "docs" / "discovery-performance.md"
M6_INDEXES = (
    "ix_listings_active_by_price",
    "ix_listings_active_by_created",
    "ix_events_name_trgm",
    "ix_events_status_starts",
    "ix_events_city",
    "ix_tickets_category",
)
# One page should never need to look at more listings than this. A sequential scan is not the
# only way to read a whole table: an index in sort order, filtered row by row, reads it too.
MAX_LISTINGS_READ = 10_000
LIMIT = 20
PAGE = 500
RUNS = 15
PAGE_RUNS = 101
MAX_SLOWDOWN = 1.20


@dataclass(frozen=True)
class Case:
    name: str
    stmt: Select[Any]


@dataclass(frozen=True)
class Plan:
    execution_ms: float
    listings_scans: tuple[str, ...]
    other_scans: tuple[str, ...]
    listings_read: int

    @property
    def scans_whole_table(self) -> bool:
        return any(scan.startswith("Seq Scan") for scan in self.listings_scans)


def cases(event_id: Any) -> list[Case]:
    filters = {
        "no filter": ListingFilters(),
        "q=live (common)": ListingFilters(q="live"),
        "q=sharma cup (rare)": ListingFilters(q="sharma cup"),
        "q=no such event": ListingFilters(q="no such event"),
        "category=SPORTS": ListingFilters(category=EventCategory.SPORTS),
        "city=Mumbai (20%)": ListingFilters(city="Mumbai"),
        "city=Shillong (2%)": ListingFilters(city="Shillong"),
        "city=Bangalore (0%)": ListingFilters(city="Bangalore"),
        "ticket_category=BACKSTAGE (3%)": ListingFilters(ticket_category="BACKSTAGE"),
        "ticket_category=NONE (0%)": ListingFilters(ticket_category="NONE"),
        "min_price=15,000": ListingFilters(min_price_paise=1_500_000),
        "max_price=500": ListingFilters(max_price_paise=50_000),
        "price 5,000 to 6,000": ListingFilters(min_price_paise=500_000, max_price_paise=600_000),
        "event_id": ListingFilters(event_id=event_id),
        "q, city and price": ListingFilters(q="live", city="Kochi", max_price_paise=900_000),
    }
    found = [
        Case(f"{name}, sort {sort}", page_stmt(search_stmt(f), LISTING_SORTS[sort], None, LIMIT))
        for name, f in filters.items()
        for sort in (
            (ListingSort.PRICE, ListingSort.NEWEST) if name != "no filter" else ListingSort
        )
    ]
    found.append(Case("events, no filter", _events(EventFilters())))
    found.append(Case("events, q=cup", _events(EventFilters(q="cup"))))
    return found


def _events(filters: EventFilters) -> Select[Any]:
    return page_stmt(published_events_stmt(filters), SOONEST_FIRST, None, LIMIT)


def explain(connection: Connection, stmt: Select[Any]) -> dict[str, Any]:
    compiled = stmt.compile(connection, compile_kwargs={"render_postcompile": True})
    sql = f"EXPLAIN (ANALYZE, FORMAT JSON) {compiled}"
    return connection.exec_driver_sql(sql, compiled.params).scalar_one()[0]


def measure(connection: Connection, stmt: Select[Any], runs: int = RUNS) -> Plan:
    explained = [explain(connection, stmt) for _ in range(runs)]
    listings, others = [], []
    read = 0
    for node in _nodes(explained[-1]["Plan"]):
        if node.get("Relation Name") == "listings":
            seen = node["Actual Rows"] + node.get("Rows Removed by Filter", 0)
            read += round(seen * node["Actual Loops"])
        if "Scan" not in node["Node Type"] or node["Node Type"] == "Bitmap Heap Scan":
            continue
        index = node.get("Index Name", "")
        label = node["Node Type"] + (f" using {index}" if index else "")
        # A bitmap index scan names only its index; every index on listings starts ix_listings,
        # uq_listings or pk_listings.
        on_listings = node.get("Relation Name") == "listings" or "_listings" in index
        (listings if on_listings else others).append(label)
    return Plan(
        execution_ms=statistics.median(e["Execution Time"] for e in explained),
        listings_scans=tuple(dict.fromkeys(listings)),
        other_scans=tuple(dict.fromkeys(others)),
        listings_read=read,
    )


def _nodes(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node.get("Plans", ()):
        yield from _nodes(child)


def measure_before(engine: Engine, found: list[Case]) -> dict[str, Plan]:
    """The same queries without M6's indexes. The drop is rolled back, never committed."""
    with engine.connect() as connection, connection.begin() as transaction:
        for index in M6_INDEXES:
            connection.execute(text(f"DROP INDEX {index}"))
        plans = {case.name: measure(connection, case.stmt, runs=5) for case in found}
        transaction.rollback()
    return plans


def cursor_to_page(session: Session, sort: ListingSort, page: int) -> str:
    """The cursor page `page` starts from, built from the last row of the page before it."""
    stmt = search_stmt(ListingFilters())
    _, cursor = paginate(session, stmt, LISTING_SORTS[sort], None, (page - 1) * LIMIT)
    assert cursor is not None, "not enough listings to reach that page"
    return cursor


def measure_pages(engine: Engine, sort: ListingSort) -> dict[str, float]:
    stmt = search_stmt(ListingFilters())
    order = LISTING_SORTS[sort]
    with Session(engine) as session:
        cursor = cursor_to_page(session, sort, PAGE)
        first, last = (page_stmt(stmt, order, c, LIMIT) for c in (None, cursor))
        offset = page_stmt(stmt, order, None, LIMIT).offset((PAGE - 1) * LIMIT)
        connection = session.connection()
        measure(connection, first)  # warm the cache for whichever comes first
        stmts = {"page 1": first, f"page {PAGE}, keyset": last, f"page {PAGE}, OFFSET": offset}
        times: dict[str, list[float]] = {name: [] for name in stmts}
        # Taken in turns, not one after the other: at 0.15 ms anything else the laptop does is
        # a large part of a run, and this way it falls on all three alike.
        for _ in range(PAGE_RUNS):
            for name, stmt_ in stmts.items():
                times[name].append(explain(connection, stmt_)["Execution Time"])
        return {name: statistics.median(taken) for name, taken in times.items()}


def report(
    engine: Engine,
    found: list[Case],
    now: dict[str, Plan],
    before: dict[str, Plan],
    pages: dict[ListingSort, dict[str, float]],
    failures: list[str],
) -> str:
    with engine.connect() as connection:
        version = connection.scalar(text("SHOW server_version"))
        total = connection.scalar(text("SELECT count(*) FROM listings"))
        active = connection.scalar(text("SELECT count(*) FROM listings WHERE status = 'ACTIVE'"))
        events = connection.scalar(text("SELECT count(*) FROM events"))
    lines = [
        "# Discovery performance",
        "",
        "Written by `scripts/explain_discovery.py`; do not edit by hand. Data from "
        "`scripts/seed_discovery.py --reset`.",
        "",
        f"- **Data:** {events:,} events, {total:,} listings ({active:,} ACTIVE).",
        f"- **Where:** PostgreSQL {version} in Docker on {platform.system()} "
        f"({platform.processor()}), default Postgres settings, warm cache.",
        f"- **Times:** `EXPLAIN (ANALYZE)` execution time, median of {RUNS} runs "
        f"({PAGE_RUNS} for the page comparison). A page is {LIMIT} rows.",
        f"- **Result:** {'PASS' if not failures else 'FAIL: ' + '; '.join(failures)}",
        "",
        f"## Page 1 against page {PAGE}",
        "",
        f"| Sort | Page 1 | Page {PAGE}, keyset | Page {PAGE}, `OFFSET` | Keyset vs page 1 "
        "| OFFSET vs page 1 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for sort, times in pages.items():
        first, keyset, offset = times.values()
        lines.append(
            f"| `{sort}` | {first:.2f} ms | {keyset:.2f} ms | {offset:.2f} ms "
            f"| {keyset / first:.2f}x | {offset / first:.1f}x |"
        )
    lines += [
        "",
        "## Every query",
        "",
        "How each query reaches `listings` (and any M6 index it uses on `events` or `tickets`), "
        "its time without and with M6's indexes, and how many listings it reads to fill the page "
        f"(at most {MAX_LISTINGS_READ:,} to pass). The query is the API's own, joins to `events` "
        "and `tickets` included.",
        "",
        "| Query | Before | After | Listings read | Plan before | Plan after |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in found:
        old, new = before[case.name], now[case.name]
        lines.append(
            f"| {case.name} | {old.execution_ms:.2f} ms | {new.execution_ms:.2f} ms "
            f"| {new.listings_read:,} | {_scans(old)} | {_scans(new)} |"
        )
    return "\n".join(lines) + "\n"


def _scans(plan: Plan) -> str:
    # The joins to events and tickets by primary key say nothing; an M6 index on them does.
    named = tuple(s for s in plan.other_scans if "_events_" in s or "ix_tickets" in s)
    return ", ".join(plan.listings_scans + named or plan.other_scans) or "none"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-report", action="store_true", help="check only; write no file")
    args = parser.parse_args(argv)

    settings = get_settings()
    name = make_url(settings.database_url).database
    if not is_throwaway(name, ("_perf",)):
        print(f"Refusing: {name!r} does not end in _perf. Set DATABASE_URL.", file=sys.stderr)
        return 2
    engine = create_db_engine(settings)
    try:
        with engine.connect() as connection:
            event_id = connection.scalar(select(Listing.event_id).limit(1))
            found = cases(event_id)
            now = {case.name: measure(connection, case.stmt) for case in found}
        before = measure_before(engine, found)
        pages = {sort: measure_pages(engine, sort) for sort in ListingSort}

        failures = [f"{n}: scans all of listings" for n, p in now.items() if p.scans_whole_table]
        failures += [
            f"{n}: reads {p.listings_read:,} listings for one page"
            for n, p in now.items()
            if p.listings_read > MAX_LISTINGS_READ
        ]
        for sort, times in pages.items():
            first, keyset, _ = times.values()
            if keyset > first * MAX_SLOWDOWN:
                failures.append(f"{sort}: page {PAGE} is {keyset / first:.2f}x page 1")
        body = report(engine, found, now, before, pages, failures)
    finally:
        engine.dispose()

    print(body)
    if not args.no_report:
        REPORT.write_bytes(body.encode())
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
