"""The errors events code raises. Each becomes a problem+json response with its own `type`."""

from app.core.exceptions import AppError, ConflictError, NotFoundError


class EventNotFoundError(NotFoundError):
    code = "event-not-found"
    title = "Event not found"


class ProviderNotFoundError(NotFoundError):
    code = "provider-not-found"
    title = "Ticket provider not found"


class EventInPastError(AppError):
    status = 422
    code = "event-in-past"
    title = "The event must start in the future"


class IllegalStatusChangeError(ConflictError):
    code = "illegal-status-change"
    title = "This status change is not allowed"


class EventNotEditableError(ConflictError):
    code = "event-not-editable"
    title = "This event can no longer be changed"


class ProviderCodeTakenError(ConflictError):
    code = "provider-code-taken"
    title = "A provider with this code already exists"
