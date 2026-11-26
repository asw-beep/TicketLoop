"""Webhooks from the gateway (hard problem 3): verify, store once, apply once.

The order matters. The signature is checked over the exact bytes received, before anything
parses them. The event row and what it changes are one transaction, so either both happened or
neither did, and the gateway's next try starts clean.
"""

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Annotated

import structlog
from pydantic import AwareDatetime, BaseModel, StringConstraints, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.modules.payments import service
from app.modules.payments.errors import BadWebhookBodyError, BadWebhookSignatureError
from app.modules.payments.models import PROVIDER, EventOutcome, Payment, PaymentEvent

log = structlog.get_logger(__name__)

SIGNATURE_HEADER = "X-Fakepay-Signature"
TIMESTAMP_HEADER = "X-Fakepay-Timestamp"
MAX_AGE_SECONDS = 300

# No control characters: Postgres text cannot hold a NUL, and an id never has one.
_Id = Annotated[str, StringConstraints(min_length=1, max_length=255, pattern=r"^[\x21-\x7e]+$")]


class WebhookIn(BaseModel):
    event_id: _Id
    type: _Id
    transaction_id: _Id
    amount_paise: int
    currency: _Id
    occurred_at: AwareDatetime


def verify(
    secret: str, body: bytes, signature: str | None, timestamp: str | None, now: datetime
) -> None:
    """Raise unless the gateway signed exactly these bytes within the last five minutes."""
    if not signature or not timestamp or not timestamp.isascii() or not timestamp.isdigit():
        raise BadWebhookSignatureError()
    expected = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    # As bytes: compare_digest refuses non-ASCII text. It takes as long whichever byte differs,
    # so how long we take says nothing about how close a forger got.
    if not hmac.compare_digest(expected.encode(), signature.encode("utf-8", "replace")):
        raise BadWebhookSignatureError()
    # After the signature, so only the gateway itself can be told its clock is off. The length
    # check stops a ridiculous number before it becomes one.
    if len(timestamp) > 12 or abs(now.timestamp() - int(timestamp)) > MAX_AGE_SECONDS:
        raise BadWebhookSignatureError()


def parse(body: bytes) -> WebhookIn:
    try:
        return WebhookIn.model_validate_json(body)
    except ValidationError as error:
        raise BadWebhookBodyError() from error


def receive(db: Session, event: WebhookIn) -> EventOutcome | None:
    """Store a verified event and apply it. None if it was delivered before."""
    event_row_id = db.execute(
        insert(PaymentEvent)
        .values(
            provider=PROVIDER,
            provider_event_id=event.event_id,
            event_type=event.type,
            payload=event.model_dump(mode="json"),
        )
        # A second delivery arriving while the first is being applied waits here for it.
        .on_conflict_do_nothing(constraint="uq_payment_events_provider_event")
        .returning(PaymentEvent.id)
    ).scalar_one_or_none()
    if event_row_id is None:
        return None

    payment = db.scalar(
        select(Payment).where(
            Payment.provider == PROVIDER,
            Payment.provider_transaction_id == event.transaction_id,
        )
    )
    outcome, error = _apply(db, payment, event)
    if outcome == EventOutcome.FAILED:
        # An alert: the gateway says something happened and we could not make it so.
        log.error("payment.webhook_failed", event_id=event.event_id, error=error)
    db.execute(
        update(PaymentEvent)
        .where(PaymentEvent.id == event_row_id)
        .values(
            payment_id=payment.id if payment else None,
            processing_status=outcome,
            error=error,
            processed_at=func.clock_timestamp(),
        )
    )
    return outcome


def _apply(
    db: Session, payment: Payment | None, event: WebhookIn
) -> tuple[EventOutcome, str | None]:
    if event.type not in ("payment.succeeded", "payment.failed"):
        return EventOutcome.IGNORED, None
    if payment is None:
        # Reconciliation finds the payment by its idempotency key and settles it.
        return EventOutcome.FAILED, "no payment has this transaction id"
    if (event.amount_paise, event.currency) != (payment.amount_paise, payment.currency):
        return EventOutcome.FAILED, (
            f"amount mismatch: the event says {event.amount_paise} {event.currency}, "
            f"the order {payment.amount_paise} {payment.currency}"
        )
    snapshot = event.model_dump(mode="json")
    try:
        # A SAVEPOINT: if a row is not in the state it should be, keep the event and say so,
        # rather than answering an error the gateway would retry for ever.
        with db.begin_nested():
            if event.type == "payment.succeeded":
                applied = service.succeed(db, payment.id, source="webhook", snapshot=snapshot)
            else:
                applied = service.fail(
                    db, payment.id, reason="declined", source="webhook", snapshot=snapshot
                )
    except AppError as error:
        return EventOutcome.FAILED, f"{error.code}: {error}"
    return (EventOutcome.PROCESSED if applied else EventOutcome.IGNORED), None


def now() -> datetime:
    return datetime.now(UTC)
