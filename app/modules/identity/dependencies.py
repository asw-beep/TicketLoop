"""Who is calling? Route guards that every protected endpoint declares as a parameter:

    @router.get("/tickets/me")
    def my_tickets(user: CurrentUser, db: DbSession) -> ...: ...

    @router.post("/admin/events")
    def create_event(admin: AdminUser, db: DbSession) -> ...: ...

401 means "I do not know who you are" (no token, bad token, expired token). 403 means "I know
who you are and you may not" (suspended, or not an admin).
"""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.dependencies import AppSettings
from app.core.exceptions import UnauthorizedError
from app.core.security import InvalidAccessTokenError, decode_access_token
from app.infrastructure.database.session import DbSession
from app.modules.identity.errors import AccountSuspendedError, AdminRequiredError
from app.modules.identity.models import AccountStatus, User, UserRole

# Reads "Authorization: Bearer <token>" and adds the Authorize button to /docs.
# auto_error=False: a missing or non-Bearer header gives None, so we raise our own
# problem+json 401 instead of FastAPI's default error body.
bearer_scheme = HTTPBearer(auto_error=False, description="The access_token from /auth/login")


Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def get_current_user(db: DbSession, settings: AppSettings, credentials: Credentials) -> User:
    if credentials is None:
        raise UnauthorizedError("Send the access token as 'Authorization: Bearer <token>'.")
    return _user_from_token(db, settings, credentials.credentials)


def get_optional_user(
    db: DbSession, settings: AppSettings, credentials: Credentials
) -> User | None:
    """For routes anyone may call, whose answer depends on who is calling. No header means a
    guest. A header with a bad or expired token is still a 401: the client should refresh,
    not be shown the guest's view without knowing why."""
    if credentials is None:
        return None
    return _user_from_token(db, settings, credentials.credentials)


def _user_from_token(db: DbSession, settings: AppSettings, token: str) -> User:
    try:
        claims = decode_access_token(token, settings)
    except InvalidAccessTokenError:
        raise UnauthorizedError("The access token is invalid or has expired.") from None

    # The signature proves we issued the token; it cannot prove the account is still fine.
    # One primary-key lookup per request is the price of suspension and role changes taking
    # effect at once instead of when the token expires, up to 15 minutes later.
    user = db.get(User, claims.user_id)
    if user is None:
        raise UnauthorizedError("The access token is invalid or has expired.")
    if user.account_status == AccountStatus.SUSPENDED:
        raise AccountSuspendedError()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN:
        raise AdminRequiredError()
    return user


AdminUser = Annotated[User, Depends(require_admin)]
