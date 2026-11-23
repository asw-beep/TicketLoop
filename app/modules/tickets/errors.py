"""The errors ticket code raises. Each becomes a problem+json response with its own `type`."""

from app.core.exceptions import AppError, ConflictError, NotFoundError, PermissionDeniedError


class TicketNotFoundError(NotFoundError):
    """One error, with one fixed detail, for "no such ticket" and "not yours", so a response
    never tells anyone whether another user's ticket ID exists."""

    code = "ticket-not-found"
    title = "Ticket not found"

    def __init__(self) -> None:
        super().__init__("Ticket not found.")


class TicketAlreadyRegisteredError(ConflictError):
    code = "ticket-already-registered"
    title = "This ticket is already registered"


class EventNotOpenError(ConflictError):
    """The event is not PUBLISHED: tickets for it cannot be registered (M4) or listed (M5)."""

    code = "event-not-open"
    title = "The event is not open"


class EventStartedError(AppError):
    status = 422
    code = "event-started"
    title = "The event has already started"


class DocumentNotFoundError(NotFoundError):
    code = "document-not-found"
    title = "Document not found"


class OwnerOnlyError(PermissionDeniedError):
    """An admin can see the ticket, so a 404 would hide nothing; they review evidence, they
    do not add it."""

    code = "owner-only"
    title = "Only the ticket's owner can do this"


class OwnTicketReviewError(PermissionDeniedError):
    """Self-review is exactly what an audit log exists to catch, so it is refused outright."""

    code = "own-ticket-review"
    title = "You cannot verify a ticket you own"


class TicketNotPendingError(ConflictError):
    code = "ticket-not-pending"
    title = "This ticket has already been reviewed"


class TooManyDocumentsError(ConflictError):
    code = "too-many-documents"
    title = "This ticket already has the most documents allowed"


class FileTooLargeError(AppError):
    status = 413
    code = "file-too-large"
    title = "The file is too large"


class UnsupportedFileTypeError(AppError):
    status = 415
    code = "unsupported-file-type"
    title = "Upload a PDF, PNG or JPEG"


class EmptyFileError(AppError):
    status = 422
    code = "empty-file"
    title = "The file is empty"


class StorageUnavailableError(AppError):
    status = 503
    code = "storage-unavailable"
    title = "File storage is unavailable; try again shortly"
