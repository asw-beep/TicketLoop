"""M2 experiment: two sessions try to claim the same row at once. Watch the second one wait.

    docker compose up -d db
    uv run python -m scripts.lock_experiment    (as a module, so `import app` resolves)

Session A claims the seat and keeps its transaction open. Session B sends the same claim and
blocks. A third connection looks at B in pg_stat_activity. When A commits, B re-checks its
WHERE clause against A's new row, finds RESERVED, and updates nothing. This is exactly how
TicketLoop stops two buyers reserving one listing (docs/hard-problems.md, problem 1).

Runs in its own scratch database, created and dropped here, so your data is untouched.
"""

import threading
import time

import psycopg
from sqlalchemy import make_url

from app.core.config import get_settings

CLAIM = "UPDATE seats SET status = 'RESERVED' WHERE id = 1 AND status = 'ACTIVE' RETURNING id"
STARTED = time.perf_counter()


def dsn(database: str) -> str:
    url = make_url(get_settings().database_url).set(drivername="postgresql", database=database)
    return url.render_as_string(hide_password=False)


def say(message: str) -> None:
    print(f"{time.perf_counter() - STARTED:5.2f}s  {message}", flush=True)


def run() -> None:
    a = psycopg.connect(dsn("lock_demo"))  # psycopg sends BEGIN before the first statement
    b = psycopg.connect(dsn("lock_demo"))
    watcher = psycopg.connect(dsn("lock_demo"), autocommit=True)
    watcher.execute("CREATE TABLE seats (id int PRIMARY KEY, status text NOT NULL)")
    watcher.execute("INSERT INTO seats VALUES (1, 'ACTIVE')")

    claimed = a.execute(CLAIM).fetchall()
    say(f"A: claims the seat -> {len(claimed)} row updated, transaction left open")

    outcome: dict[str, float | int] = {}

    def b_claims() -> None:
        start = time.perf_counter()
        outcome["rows"] = len(b.execute(CLAIM).fetchall())
        outcome["waited"] = time.perf_counter() - start
        b.commit()

    thread = threading.Thread(target=b_claims)
    thread.start()
    say("B: sends the same claim")
    time.sleep(1)

    state, wait_type, wait_event, blockers = watcher.execute(
        "SELECT state, wait_event_type, wait_event, pg_blocking_pids(pid) "
        "FROM pg_stat_activity WHERE pid = %s",
        (b.info.backend_pid,),
    ).fetchone()
    say(f"watcher: B is {state}, waiting on {wait_type}/{wait_event}, blocked by {blockers}")
    say(f"         (A is pid {a.info.backend_pid})")

    time.sleep(2)
    a.commit()
    say("A: COMMIT")
    thread.join()
    say(f"B: wakes up, re-checks WHERE, updates {outcome['rows']} rows")
    say(f"   after waiting {outcome['waited']:.2f}s")
    final = watcher.execute("SELECT status FROM seats WHERE id = 1").fetchone()[0]
    say(f"final status: {final}, claimed exactly once")
    for connection in (a, b, watcher):
        connection.close()


def main() -> None:
    admin = psycopg.connect(dsn(make_url(get_settings().database_url).database), autocommit=True)
    admin.execute("DROP DATABASE IF EXISTS lock_demo WITH (FORCE)")
    admin.execute("CREATE DATABASE lock_demo")
    try:
        run()
    finally:
        admin.execute("DROP DATABASE IF EXISTS lock_demo WITH (FORCE)")
        admin.close()


if __name__ == "__main__":
    main()
