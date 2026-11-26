"""The errors notification code raises."""

from app.core.exceptions import NotFoundError


class NotificationNotFoundError(NotFoundError):
    """One error for "no such notification" and "not yours"."""

    code = "notification-not-found"
    title = "Notification not found"

    def __init__(self) -> None:
        super().__init__("Notification not found.")
