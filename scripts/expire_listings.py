"""Expire every ACTIVE listing whose expiry has passed. In Docker, Celery beat runs the same
job (app/workers/jobs.py) every 5 minutes; this is for doing it by hand.

    uv run python -m scripts.expire_listings

Runs against the database in DATABASE_URL, in one transaction. Each listing moves to EXPIRED
through the state machine and gets a `listing.expired` audit row with no actor (the system).
Running it again straight away expires nothing.
"""

import argparse
import sys

from app.core.config import get_settings
from app.infrastructure.database.engine import create_db_engine
from app.infrastructure.database.session import create_session_factory
from app.workers.jobs import expire_listings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expire ACTIVE listings past their expiry.")
    parser.parse_args(argv)

    engine = create_db_engine(get_settings())
    try:
        count = expire_listings(create_session_factory(engine))
    finally:
        engine.dispose()

    print(f"Expired {count} listing(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
