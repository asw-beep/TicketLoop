"""HTTP routes for /auth and /users. Thin: validate the body, call the service, shape the reply.

Plain `def` routes: SQLAlchemy and Argon2 both block, so FastAPI runs these in its thread
pool instead of on the event loop.

The refresh token lives only in a cookie that page scripts cannot read (HttpOnly). A script
injected into the page can still call the API while the page is open, but it cannot carry the
7-day token away. SameSite=Strict stops other sites from making the browser send it, which
is what keeps /auth/refresh and /auth/logout safe from cross-site request forgery.
"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response, status

from app.api.rate_limits import limit_login
from app.core.config import Settings
from app.core.dependencies import AppSettings
from app.infrastructure.database.session import DbSession
from app.infrastructure.external_providers.email import EmailSender
from app.modules.identity import service
from app.modules.identity.dependencies import CurrentUser
from app.modules.identity.errors import InvalidRefreshTokenError
from app.modules.identity.models import User
from app.modules.identity.schemas import (
    AccessTokenOut,
    ChangePasswordIn,
    LoginIn,
    PasswordResetConfirmIn,
    PasswordResetRequestIn,
    RegisterIn,
    UpdateMeIn,
    UserOut,
)
from app.modules.identity.service import IssuedTokens

REFRESH_COOKIE = "refresh_token"


def get_email_sender(request: Request) -> EmailSender:
    return request.app.state.email_sender


Mailer = Annotated[EmailSender, Depends(get_email_sender)]
# Cookie(...) reads the named cookie; None when the browser sent none.
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE, include_in_schema=False)]


def cookie_path(settings: Settings) -> str:
    """Only refresh and logout need the cookie, so no other request carries it."""
    return f"{settings.api_prefix}/auth"


def set_refresh_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.refresh_token_days * 24 * 3600,
        path=cookie_path(settings),
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    # The browser matches on name and path: a different path would leave the real cookie.
    response.delete_cookie(
        REFRESH_COOKIE,
        path=cookie_path(settings),
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def access_token_body(
    response: Response, settings: Settings, tokens: IssuedTokens
) -> AccessTokenOut:
    set_refresh_cookie(response, settings, tokens.refresh_token)
    return AccessTokenOut(access_token=tokens.access_token, expires_in=tokens.expires_in)


auth_router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


@auth_router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserOut)
def register(body: RegisterIn, db: DbSession) -> User:
    return service.register(
        db,
        email=body.email,
        password=body.password.get_secret_value(),
        full_name=body.full_name,
    )


@auth_router.post("/login")
def login(
    body: LoginIn, db: DbSession, settings: AppSettings, response: Response, request: Request
) -> AccessTokenOut:
    limit_login(request, body.email)
    tokens = service.login(
        db, settings, email=body.email, password=body.password.get_secret_value()
    )
    return access_token_body(response, settings, tokens)


@auth_router.post("/refresh")
def refresh(
    db: DbSession, settings: AppSettings, response: Response, refresh_token: RefreshCookie = None
) -> AccessTokenOut:
    if not refresh_token:
        raise InvalidRefreshTokenError()
    tokens = service.refresh(db, settings, refresh_token=refresh_token)
    return access_token_body(response, settings, tokens)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(db: DbSession, settings: AppSettings, refresh_token: RefreshCookie = None) -> Response:
    if refresh_token:
        service.logout(db, refresh_token=refresh_token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_refresh_cookie(response, settings)
    return response


@auth_router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(
    body: PasswordResetRequestIn,
    db: DbSession,
    settings: AppSettings,
    mailer: Mailer,
    request: Request,
) -> dict[str, str]:
    limit_login(request, body.email, name="password_reset")
    service.request_password_reset(db, settings, mailer, email=body.email)
    # The same words whether or not the account exists.
    return {"message": "If an account uses that email, a reset link is on its way."}


@auth_router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_password_reset(body: PasswordResetConfirmIn, db: DbSession) -> Response:
    service.confirm_password_reset(
        db, token=body.token, new_password=body.new_password.get_secret_value()
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@users_router.get("/me", response_model=UserOut)
def read_me(user: CurrentUser) -> User:
    return user


@users_router.patch("/me", response_model=UserOut)
def update_me(body: UpdateMeIn, user: CurrentUser, db: DbSession) -> User:
    user.full_name = body.full_name  # get_db sends the UPDATE and commits it
    return user


@users_router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: ChangePasswordIn, user: CurrentUser, db: DbSession, settings: AppSettings
) -> Response:
    service.change_password(
        db,
        user,
        current_password=body.current_password.get_secret_value(),
        new_password=body.new_password.get_secret_value(),
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_refresh_cookie(response, settings)  # its token is revoked with all the others
    return response
