"""The scheduled jobs, as functions: Celery beat runs them from M9, and the scripts in scripts/
run the same functions by hand. They live in app/ because the Docker image has no scripts/.

Each is safe to run while another copy of itself is running: expiry claims rows with
FOR UPDATE SKIP LOCKED, and settling a payment locks its reservation first.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.payments import PaymentGateway
from app.modules.idempotency.service import delete_expired
from app.modules.listings.service import expire_listings as expire_due_listings
from app.modules.payments.models import ReconciliationRun
from app.modules.payments.reconcile import Report, reconcile, resolve_overdue
from app.modules.reservations.service import EXPIRY_BATCH
from app.modules.reservations.service import expire_reservations as expire_batch
from app.modules.transfers.service import resume_stalled


@dataclass(frozen=True)
class ExpiryResult:
    expired: int
    overdue: Report


def expire_due_reservations(sessions: sessionmaker[Session], batch: int = EXPIRY_BATCH) -> int:
    """Expire every unpaid hold due now, a batch per transaction. Returns how many."""
    total = 0
    while True:
        with sessions() as session, session.begin():
            found, expired = expire_batch(session, limit=batch)
        total += expired
        # Stop when nothing is left, or when nothing left can be expired.
        if found < batch or expired == 0:
            return total


def expire_reservations(sessions: sessionmaker[Session], gateway: PaymentGateway) -> ExpiryResult:
    """Unpaid holds past their end, then holds whose buyer went to pay and never came back:
    those are settled only on the gateway's word."""
    expired = expire_due_reservations(sessions)
    return ExpiryResult(expired, resolve_overdue(sessions, gateway))


def expire_listings(sessions: sessionmaker[Session]) -> int:
    with sessions() as session, session.begin():
        return expire_due_listings(session)


def reconcile_payments(
    sessions: sessionmaker[Session], gateway: PaymentGateway
) -> tuple[ReconciliationRun, int]:
    """One recorded reconciliation run, then the idempotency keys past their 24 hours."""
    run = reconcile(sessions, gateway)
    with sessions() as session, session.begin():
        forgotten = delete_expired(session)
    return run, forgotten


def resume_transfers(sessions: sessionmaker[Session]) -> tuple[int, int]:
    """Paid orders and transfers whose message was lost: (started, resumed)."""
    with sessions() as session, session.begin():
        return resume_stalled(session)
