"""Close the holds whose time is up, every 30 seconds, until stopped. In Docker, Celery beat
runs the same job (app/workers/jobs.py) on the same schedule; this is for doing it by hand.

    uv run python -m scripts.expire_reservations            # loop; Ctrl+C to stop
    uv run python -m scripts.expire_reservations --once     # one pass

Each pass expires unpaid holds past their end, in batches, each batch in its own transaction,
and puts their tickets back on sale. Then it asks the gateway about holds whose buyer went to
pay and never came back; without an answer those stay as they are. Any number of these can run
at once: FOR UPDATE SKIP LOCKED gives each hold to exactly one of them.
"""

import argparse
import sys
import time

from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.infrastructure.database.engine import create_db_engine
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.payments import FakepayGateway
from app.modules.payments.reconcile import resolve_overdue
from app.workers.jobs import expire_due_reservations as run_once

INTERVAL_SECONDS = 30


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Close reservations whose time is up.")
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    args = parser.parse_args(argv)

    settings = get_settings()
    engine = create_db_engine(settings)
    gateway = FakepayGateway.at(settings.fakepay_url)
    try:
        sessions = create_session_factory(engine)
        while True:
            try:
                count = run_once(sessions)
                if count or args.once:
                    print(f"Expired {count} reservation(s).", flush=True)
                report = resolve_overdue(sessions, gateway)
                if report.fixed or args.once:
                    print(f"Settled {report.fixed} overdue payment(s).", flush=True)
                if report.error:
                    # Nothing was released on silence; the next pass asks again.
                    print(f"Payment gateway: {report.error}", file=sys.stderr)
            except OperationalError as error:
                # Postgres restarted or is unreachable: the next pass will find the same holds.
                if args.once:
                    raise
                print(f"Database unavailable, will retry: {error.orig}", file=sys.stderr)
            if args.once:
                return 0
            time.sleep(INTERVAL_SECONDS)
    except KeyboardInterrupt:
        return 0
    finally:
        gateway.close()
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
