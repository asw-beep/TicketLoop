"""The errors the Idempotency-Key flow raises."""

from app.core.exceptions import AppError, ConflictError


class IdempotencyKeyRequiredError(AppError):
    status = 400
    code = "idempotency-key-required"
    title = "Send an Idempotency-Key header"


class IdempotencyKeyInvalidError(AppError):
    status = 400
    code = "idempotency-key-invalid"
    title = "The Idempotency-Key must be 1 to 255 printable characters"


class IdempotencyKeyReusedError(AppError):
    """The same key with a different request: a bug in the client."""

    status = 422
    code = "idempotency-key-reused"
    title = "This Idempotency-Key was used for a different request"


class IdempotencyKeyInProgressError(ConflictError):
    code = "idempotency-key-in-progress"
    title = "The first request with this Idempotency-Key is still running"
    headers = {"Retry-After": "2"}
