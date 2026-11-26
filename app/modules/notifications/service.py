"""Notifications: a second consumer of the outbox's events, and the two routes that read them.

The consumer exists to show per-consumer deduplication: `payment.succeeded` goes to the
transfer consumer and to this one, and each handles it once, on its own, whatever the other
does. Delivery to a person's inbox is a row here; there is no email or push.
"""

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.pagination import Sort, paginate
from app.modules.identity.models import User
from app.modules.notifications.errors import NotificationNotFoundError
from app.modules.notifications.models import Notification
from app.modules.outbox.service import Consumer, EventType, first_time

NEWEST = Sort("notifications.newest", (Notification.created_at, Notification.id), descending=True)

# For each event: who hears about it, and as what. "buyer" and "seller" name payload keys.
RECIPIENTS: Mapping[str, tuple[tuple[str, str], ...]] = {
    EventType.PAYMENT_SUCCEEDED: (("buyer", "payment_succeeded"), ("seller", "ticket_sold")),
    EventType.PAYMENT_FAILED: (("buyer", "payment_failed"),),
    EventType.RESERVATION_EXPIRED: (("buyer", "hold_expired"),),
    EventType.TRANSFER_COMPLETED: (("buyer", "ticket_received"), ("seller", "ticket_handed_over")),
    EventType.TRANSFER_FAILED: (
        ("buyer", "transfer_needs_review"),
        ("seller", "transfer_needs_review"),
    ),
}
# What a notification may carry: things to link to, and why. Never the other party's id.
SHOWN = ("order_id", "listing_id", "reservation_id", "reason")


def notify(db: Session, event_id: uuid.UUID, event_type: str, payload: Mapping[str, Any]) -> int:
    """In the caller's transaction. Returns how many notifications were written."""
    if not first_time(db, Consumer.NOTIFY, event_id):
        return 0
    shown = {key: payload[key] for key in SHOWN if key in payload}
    written = 0
    for role, kind in RECIPIENTS.get(event_type, ()):
        user_id = payload.get(f"{role}_id")
        if user_id is None:
            continue
        # RETURNING, not rowcount: an ORM insert does not report a row count.
        new = db.execute(
            insert(Notification)
            .values(
                user_id=uuid.UUID(str(user_id)),
                notification_type=kind,
                payload=shown,
                source_event_id=event_id,
            )
            .on_conflict_do_nothing(constraint="uq_notifications_event_user")
            .returning(Notification.id)
        ).scalar_one_or_none()
        written += new is not None
    return written


def list_notifications(
    db: Session, user: User, unread_only: bool, cursor: str | None, limit: int
) -> tuple[list[Notification], str | None]:
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return paginate(db, stmt, NEWEST, cursor, limit)


def mark_read(db: Session, notification_id: uuid.UUID, user: User) -> Notification:
    """Set read_at the first time; a repeat changes nothing and returns the same row."""
    notification = db.get(Notification, notification_id)
    if notification is None or notification.user_id != user.id:
        raise NotificationNotFoundError()
    db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.read_at.is_(None))
        .values(read_at=func.now())
    )
    db.refresh(notification)
    return notification
