"""HTTP routes for notifications. Thin: validate, call the service, shape."""

import uuid

from fastapi import APIRouter

from app.core.pagination import Cursor, Limit, Page
from app.infrastructure.database.session import DbSession
from app.modules.identity.dependencies import CurrentUser
from app.modules.notifications import service
from app.modules.notifications.schemas import NotificationOut

notifications_router = APIRouter(prefix="/notifications", tags=["notifications"])


@notifications_router.get("", response_model=Page[NotificationOut])
def my_notifications(
    user: CurrentUser,
    db: DbSession,
    unread_only: bool = False,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> Page[NotificationOut]:
    """Your notifications, newest first."""
    items, next_cursor = service.list_notifications(db, user, unread_only, cursor, limit)
    return Page[NotificationOut](items=items, next_cursor=next_cursor)


@notifications_router.patch("/{notification_id}/read", response_model=NotificationOut)
def read_notification(
    notification_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> NotificationOut:
    """Mark one of yours as read. Asking again is harmless."""
    return NotificationOut.model_validate(service.mark_read(db, notification_id, user))
