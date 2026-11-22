"""Passwords, access tokens and opaque tokens. Pure functions: no database, no HTTP.

Three kinds of secret, three treatments:

- **Passwords** are hashed with Argon2id, a deliberately slow, memory-hard hash. People pick
  guessable passwords, so a stolen hash must cost an attacker real time and memory per guess.
- **Access tokens** are JWTs signed with HS256. Any API process can check one without a
  database lookup for the signature. They live 15 minutes because a JWT cannot be revoked.
- **Refresh and password-reset tokens** are opaque: 32 random bytes, meaningless on their
  own. The database stores only their SHA-256. A fast hash is enough here, unlike for
  passwords: 256 random bits cannot be guessed, however fast each guess is.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache

import jwt
from argon2 import PasswordHasher, profiles
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import Settings

# ---------------------------------------------------------------- passwords

# RFC 9106's second recommended profile: Argon2id, 64 MiB, 3 passes, 4 lanes, 16-byte salt.
# Above OWASP's minimum (19 MiB, 2 passes). Each hash costs about 50 ms of CPU and 64 MiB of
# memory, which is what makes guessing slow. Tests swap in a cheap hasher (tests/conftest.py).
PASSWORD_HASHER = PasswordHasher.from_parameters(profiles.RFC_9106_LOW_MEMORY)
_hasher = PASSWORD_HASHER


def hash_password(password: str) -> str:
    """The PHC string: algorithm, parameters, salt and hash in one value, e.g.
    $argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>. Verification reads the parameters from it."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):  # wrong password, or not an Argon2 hash
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True when the hash was made with weaker parameters than we use now."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


@cache
def _dummy_hash() -> str:
    return _hasher.hash(secrets.token_urlsafe(16))


def burn_password_check(password: str) -> None:
    """Spend the time of a real verification when the email is unknown.

    Without it, "no such user" answers in under a millisecond and "wrong password" takes
    about 50 ms, so response time alone would tell an attacker which emails have accounts.
    """
    verify_password(_dummy_hash(), password)


# ---------------------------------------------------------------- access tokens

# Fixed in code, never read from config or from the token's own header. Trusting the header's
# "alg" is how "alg": "none" and key-confusion attacks work.
JWT_ALGORITHM = "HS256"


class InvalidAccessTokenError(Exception):
    """Bad signature, expired, wrong issuer, malformed: the caller only needs to know it failed."""


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    issued_at: datetime
    expires_at: datetime
    token_id: str


def create_access_token(
    user_id: uuid.UUID, settings: Settings, *, now: datetime | None = None
) -> str:
    issued_at = now or datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iss": settings.jwt_issuer,
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=settings.access_token_minutes),
        # A unique id per token, so log lines can name a token without containing it.
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret_key.get_secret_value(), JWT_ALGORITHM)


def decode_access_token(token: str, settings: Settings) -> AccessTokenClaims:
    """Check the signature, expiry and issuer, then return the claims we use."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "iss", "iat", "exp", "jti"]},
        )
        user_id = uuid.UUID(payload["sub"])
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise InvalidAccessTokenError(type(exc).__name__) from exc
    return AccessTokenClaims(
        user_id=user_id,
        issued_at=datetime.fromtimestamp(payload["iat"], UTC),
        expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        token_id=payload["jti"],
    )


# ---------------------------------------------------------------- opaque tokens


def new_opaque_token() -> str:
    """32 bytes from the OS's secure random source, as 43 URL-safe characters."""
    return secrets.token_urlsafe(32)


def hash_opaque_token(token: str) -> str:
    """What the database stores. Someone who reads the table cannot use what they find."""
    return hashlib.sha256(token.encode()).hexdigest()
