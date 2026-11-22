"""HTTP routes for /auth and /users. Thin: validate the body, call the service, shape the reply.

Plain `def` routes: SQLAlchemy and Argon2 both block, so FastAPI runs these in its thread
pool instead of on the event loop.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.core.dependencies import AppSettings
from app.infrastructure.database.session import DbSession
from app.infrastructure.external_providers.email import EmailSender
from app.modules.identity import service
from app.modules.identity.dependencies import CurrentUser
from app.modules.identity.models import User
from app.modules.identity.schemas import (
    ChangePasswordIn,
    LoginIn,
    PasswordResetConfirmIn,
    PasswordResetRequestIn,
    RefreshIn,
    RegisterIn,
    TokenPair,
    UpdateMeIn,
    UserOut,
)


def get_email_sender(request: Request) -> EmailSender:
    return request.app.state.email_sender


Mailer = Annotated[EmailSender, Depends(get_email_sender)]

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


@auth_router.post("/login", response_model=TokenPair)
def login(body: LoginIn, db: DbSession, settings: AppSettings) -> TokenPair:
    return service.login(db, settings, email=body.email, password=body.password.get_secret_value())


@auth_router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshIn, db: DbSession, settings: AppSettings) -> TokenPair:
    return service.refresh(db, settings, refresh_token=body.refresh_token)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: RefreshIn, user: CurrentUser, db: DbSession) -> Response:
    service.logout(db, user, refresh_token=body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@auth_router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(
    body: PasswordResetRequestIn, db: DbSession, settings: AppSettings, mailer: Mailer
) -> dict[str, str]:
    service.request_password_reset(db, settings, mailer, email=body.email)
    # The same words whether or not the account exists.
    return {"message": "If an account uses that email, a reset code is on its way."}


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
def change_password(body: ChangePasswordIn, user: CurrentUser, db: DbSession) -> Response:
    service.change_password(
        db,
        user,
        current_password=body.current_password.get_secret_value(),
        new_password=body.new_password.get_secret_value(),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
