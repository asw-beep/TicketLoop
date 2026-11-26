"""Compare every payment left open with what the gateway says, and repair the difference. Celery
beat does it from M9.

    uv run python -m scripts.reconcile_payments

Writes one reconciliation_runs row and prints it. Exits 1 if the gateway did not answer. Also
deletes idempotency keys past their 24 hours. Safe to run at any time, any number at once:
settling a payment locks its reservation, so one of them settles it and the rest find it settled.
"""

import argparse
import sys

from app.core.config import get_settings
from app.infrastructure.database.engine import create_db_engine
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.payments import FakepayGateway
from app.modules.payments.models import RunStatus
from app.workers.jobs import reconcile_payments


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Reconcile open payments.").parse_args(argv)

    settings = get_settings()
    engine = create_db_engine(settings)
    gateway = FakepayGateway.at(settings.fakepay_url)
    try:
        run, forgotten = reconcile_payments(create_session_factory(engine), gateway)
    finally:
        gateway.close()
        engine.dispose()

    print(f"{run.status}: checked {run.checked_count}, fixed {run.fixed_count}.")
    for finding in run.findings:
        print(
            f"  payment {finding['payment_id']}: {finding['local']} here, "
            f"{finding['gateway']} there; {finding['action']}"
        )
    if run.error:
        print(f"  the gateway did not always answer: {run.error}", file=sys.stderr)
    if forgotten:
        print(f"Deleted {forgotten} idempotency key(s) past their 24 hours.")
    return 0 if run.status == RunStatus.SUCCEEDED else 1


if __name__ == "__main__":
    sys.exit(main())
