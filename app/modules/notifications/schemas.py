"""Response bodies for /notifications."""

import uuid
from typing import Any

from pydantic import AwareDatetime

from app.core.schemas import ResponseModel


class NotificationOut(ResponseModel):
    id: uuid.UUID
    notification_type: str
    payload: dict[str, Any]
    read_at: AwareDatetime | None
    created_at: AwareDatetime
