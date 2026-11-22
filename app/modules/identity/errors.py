"""The errors identity code raises. Each becomes a problem+json response with its own `type`."""

from app.core.exceptions import AppError, ConflictError, PermissionDeniedError, UnauthorizedError


class EmailTakenError(ConflictError):
    code = "email-taken"
    title = "This email is already registered"


class InvalidCredentialsError(UnauthorizedError):
    """One error for "no such email" and "wrong password", so login cannot be used to find
    out who has an account."""

    code = "invalid-credentials"
    title = "Wrong email or password"


class AccountSuspendedError(PermissionDeniedError):
    code = "account-suspended"
    title = "This account is suspended"


class InvalidRefreshTokenError(UnauthorizedError):
    code = "invalid-refresh-token"
    title = "Refresh token is invalid, expired or revoked"


class WrongPasswordError(UnauthorizedError):
    code = "wrong-password"
    title = "The current password is wrong"


class InvalidResetTokenError(AppError):
    status = 400
    code = "invalid-reset-token"
    title = "Reset token is invalid, expired or already used"


class AdminRequiredError(PermissionDeniedError):
    code = "admin-required"
    title = "Only admins can do this"
