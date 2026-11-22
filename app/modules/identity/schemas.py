"""Request and response bodies for /auth and /users (docs/api-contract.md)."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, EmailStr, Field, Secret, StringConstraints

from app.core.schemas import RequestModel, ResponseModel


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


# Trimmed, checked by email-validator (syntax only, no DNS lookup), then lowercased, which
# is how the users table stores it (ck_users_email_lowercase).
Email = Annotated[EmailStr, BeforeValidator(_strip), AfterValidator(str.lower)]

# Secret[...] prints as '**********' in reprs and logs; the value is only read on purpose,
# with .get_secret_value(). 12 to 128 characters and no other rule, as OWASP advises: length
# matters, forced symbols do not. Argon2 has no 72-byte limit, unlike bcrypt.
NewPassword = Secret[Annotated[str, StringConstraints(min_length=12, max_length=128)]]
# Login does not re-check today's length rule, but still caps the length, so nobody can make
# us hash a megabyte of input.
GivenPassword = Secret[Annotated[str, StringConstraints(min_length=1, max_length=128)]]

FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

# Our opaque tokens are 43 characters. Anything else of plausible size is still looked up and
# refused with 401 like any wrong token; the cap only stops absurd inputs.
OpaqueToken = Annotated[str, Field(min_length=1, max_length=1000)]


class RegisterIn(RequestModel):
    email: Email
    password: NewPassword
    full_name: FullName


class LoginIn(RequestModel):
    email: Email
    password: GivenPassword


class RefreshIn(RequestModel):
    refresh_token: OpaqueToken


class UpdateMeIn(RequestModel):
    full_name: FullName


class ChangePasswordIn(RequestModel):
    current_password: GivenPassword
    new_password: NewPassword


class PasswordResetRequestIn(RequestModel):
    email: Email


class PasswordResetConfirmIn(RequestModel):
    token: OpaqueToken
    new_password: NewPassword


class UserOut(ResponseModel):
    """`User` in the API contract. No password_hash: the field is simply not declared."""

    id: uuid.UUID
    email: str
    full_name: str
    role: str
    account_status: str
    created_at: datetime


class TokenPair(ResponseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds until the access token expires
