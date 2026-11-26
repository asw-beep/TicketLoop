"""Finding out from the gateway what became of a payment, and making our rows agree.

Two callers: reconciliation (hard problem 6), which looks at every payment open for too long,
and the expiry job (hard problem 5), which looks at holds whose buyer went to pay and never
came back. Neither ever decides anything on silence.

The gateway is asked outside any transaction; each repair is a transaction of its own, through
the same succeed() and fail() the webhook uses.
"""

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.payments import GatewayIntent, GatewayUnavailable, PaymentGateway
from app.modules.orders.models import Order
from app.modules.payments import service
from app.modules.payments.models import (
    OPEN_PAYMENT_STATUSES,
    Payment,
    PaymentStatus,
    ReconciliationRun,
    RunStatus,
)
from app.modules.reservations.models import Reservation, ReservationStatus

log = structlog.get_logger(__name__)

# Long enough for any request still talking to the gateway to have finished or given up.
OPEN_FOR = timedelta(minutes=5)
# After a hold's end: the gateway's own 15 minutes for paying are over too.
GRACE = timedelta(minutes=15)
BATCH = 500
STALE_RUN = timedelta(minutes=30)
# Unanswered lookups in a row before a run stops asking: the gateway is down, not one payment.
GIVE_UP_AFTER = 3

Sessions = sessionmaker[Session]


@dataclass(frozen=True)
class Finding:
    """Where we and the gateway differed, and what was done about it."""

    payment_id: uuid.UUID
    local: str
    gateway: str
    action: str
    fixed: bool

    def as_json(self) -> dict[str, Any]:
        return {
            "payment_id": str(self.payment_id),
            "local": self.local,
            "gateway": self.gateway,
            "action": self.action,
            "fixed": self.fixed,
        }


@dataclass
class Report:
    checked: int = 0
    findings: list[Finding] = field(default_factory=list)
    error: str | None = None

    @property
    def fixed(self) -> int:
        return sum(f.fixed for f in self.findings)


class Unanswered(GatewayUnavailable):
    """No answer about one payment. `finding` says so, for the run's record."""

    def __init__(self, finding: Finding, error: GatewayUnavailable) -> None:
        super().__init__(str(error))
        self.finding = finding


def settle_from_gateway(
    sessions: Sessions, gateway: PaymentGateway, payment_id: uuid.UUID, *, source: str
) -> Finding | None:
    """Ask about one payment and apply the answer. None if there was nothing to do.
    Raises Unanswered (a GatewayUnavailable) when there is no answer, and changes nothing."""
    with sessions() as db:
        payment = db.get(Payment, payment_id)
        if payment is None or payment.status not in OPEN_PAYMENT_STATUSES:
            return None
        # Chosen at the start of the run, which may have been a while ago: its buyer may be
        # back, and asking the gateway for the intent we would not find.
        if payment.updated_at > db.scalar(select(func.now() - OPEN_FOR)):
            return None
        local, seen_at = payment.status, payment.updated_at
        transaction_id, key = payment.provider_transaction_id, payment.idempotency_key

    def finding(gateway_said: str, action: str, fixed: bool) -> Finding:
        return Finding(payment_id, local, gateway_said, action, fixed)

    try:
        if transaction_id is None:
            intent = gateway.by_idempotency_key(key)
        else:
            intent = gateway.by_transaction(transaction_id)
    except GatewayUnavailable as error:
        raise Unanswered(finding("no answer", "none: no answer", fixed=False), error) from error

    if intent is None and transaction_id is not None:
        # It gave us this id and now does not know it. That is not "unpaid"; it is a fault.
        log.error("payment.unknown_at_gateway", payment_id=str(payment_id))
        return finding("unknown", "none: the gateway lost it", fixed=False)

    with sessions() as db, db.begin():
        if intent is None:
            # The process died between saving the payment and asking the gateway, or the
            # gateway was down. The buyer may try again until the hold ends, and the next try
            # makes the intent; if they are trying right now, updated_at has moved. Either
            # way this does nothing.
            done = service.fail(
                db,
                payment_id,
                reason="intent_never_created",
                source=source,
                untouched_since=seen_at,
                once_hold_ended=True,
            )
            return finding("no intent", "failed the payment", fixed=True) if done else None

        attached = local == PaymentStatus.CREATED
        if attached:
            payment = service.attach_intent(db, payment_id, intent)
        else:
            payment = db.get_one(Payment, payment_id)
        action, done = apply(db, payment, intent, source)
        if done:
            return finding(intent.status, action, fixed=True)
        if action:
            return finding(intent.status, action, fixed=False)
        return finding(intent.status, "kept its transaction id", fixed=True) if attached else None


def apply(db: Session, payment: Payment, intent: GatewayIntent, source: str) -> tuple[str, bool]:
    """Make the payment agree with what the gateway says of its intent. (what was done or why
    not, whether it was done); ("", False) when there is nothing to do."""
    if intent.status == "open":
        return "", False
    if (intent.amount_paise, intent.currency) != (payment.amount_paise, payment.currency):
        log.error("payment.amount_mismatch", payment_id=str(payment.id))
        return "none: the amounts differ", False
    match intent.status:
        case "succeeded":
            done = service.succeed(db, payment.id, source=source, snapshot=intent.snapshot)
            action = "marked it paid"
        case "failed":
            done = service.fail(
                db, payment.id, reason="declined", source=source, snapshot=intent.snapshot
            )
            action = "failed the payment"
        case "expired":
            done = service.fail(
                db,
                payment.id,
                reason="timeout",
                source=source,
                snapshot=intent.snapshot,
                timed_out=True,
            )
            action = "timed it out"
    return (action, True) if done else ("", False)


def _settle_all(
    sessions: Sessions, gateway: PaymentGateway, payment_ids: list[uuid.UUID], source: str
) -> Report:
    report = Report()
    unanswered = 0  # in a row
    for payment_id in payment_ids:
        try:
            found = settle_from_gateway(sessions, gateway, payment_id, source=source)
        except Unanswered as error:
            # One payment the gateway will not talk about must not hide all the others; a
            # gateway that answers nothing would only time out again. The next run starts over.
            report.error = str(error)
            report.findings.append(error.finding)
            log.warning(
                "payment.gateway_unavailable",
                payment_id=str(payment_id),
                error=str(error),
                source=source,
            )
            unanswered += 1
            if unanswered == GIVE_UP_AFTER:
                break
            continue
        except Exception as error:  # one broken payment must not stop the others
            log.exception("payment.settle_failed", payment_id=str(payment_id), source=source)
            found = Finding(payment_id, "?", "?", f"error: {error}", fixed=False)
        unanswered = 0
        report.checked += 1
        if found is not None:
            report.findings.append(found)
    return report


def resolve_overdue(sessions: Sessions, gateway: PaymentGateway) -> Report:
    """Holds whose buyer went to pay and whose time ran out GRACE ago. The clock alone never
    releases these: the gateway is asked, and without an answer they stay as they are."""
    with sessions() as db:
        overdue = db.scalars(
            select(Payment.id)
            .join(Order, Order.id == Payment.order_id)
            .join(Reservation, Reservation.id == Order.reservation_id)
            .where(
                Reservation.status == ReservationStatus.PAYMENT_PENDING,
                Reservation.expires_at <= func.now() - GRACE,
                Payment.status.in_(OPEN_PAYMENT_STATUSES),
                # Not one its buyer has come back to: that request may be asking the
                # gateway for the intent we would not find.
                Payment.updated_at <= func.now() - OPEN_FOR,
            )
            .order_by(Reservation.expires_at)
            .limit(BATCH)
        ).all()
    return _settle_all(sessions, gateway, list(overdue), "expiry")


def reconcile(sessions: Sessions, gateway: PaymentGateway) -> ReconciliationRun:
    """One recorded run over every payment left open for OPEN_FOR."""
    with sessions() as db, db.begin():
        db.execute(
            update(ReconciliationRun)
            .where(
                ReconciliationRun.status == RunStatus.RUNNING,
                ReconciliationRun.started_at < func.now() - STALE_RUN,
            )
            .values(status=RunStatus.FAILED, finished_at=func.now(), error="the run died")
        )
        run = ReconciliationRun()
        db.add(run)
        db.flush()
        candidates = db.scalars(
            select(Payment.id)
            .where(
                Payment.status.in_(OPEN_PAYMENT_STATUSES),
                Payment.updated_at <= func.now() - OPEN_FOR,
            )
            .order_by(Payment.updated_at)
            .limit(BATCH)
        ).all()

    report = _settle_all(sessions, gateway, list(candidates), "reconciliation")

    with sessions() as db, db.begin():
        run = db.get_one(ReconciliationRun, run.id)
        run.status = RunStatus.FAILED if report.error else RunStatus.SUCCEEDED
        run.finished_at = func.now()
        run.checked_count = report.checked
        run.fixed_count = report.fixed
        run.findings = [f.as_json() for f in report.findings]
        run.error = report.error
        db.flush()
        db.refresh(run)
    return run
