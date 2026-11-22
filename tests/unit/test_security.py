"""Password hashing, access tokens and opaque tokens: app/core/security.py."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import ValidationError

from app.core import security
from app.core.config import Settings
from app.core.security import (
    InvalidAccessTokenError,
    create_access_token,
    decode_access_token,
    hash_opaque_token,
    hash_password,
    new_opaque_token,
    verify_password,
)

PASSWORD = "correct horse battery staple"


@pytest.fixture
def real_hasher(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the cheap test hasher from tests/conftest.py, for tests about the real settings."""
    monkeypatch.setattr(security, "_hasher", security.PASSWORD_HASHER)


# ---------------------------------------------------------------- passwords


@pytest.mark.usefixtures("real_hasher")
def test_passwords_are_hashed_with_argon2id_at_owasp_strength() -> None:
    stored = hash_password(PASSWORD)

    assert PASSWORD not in stored
    # RFC 9106's low-memory profile: 64 MiB of memory, 3 passes, 4 lanes. OWASP's floor is
    # 19 MiB and 2 passes.
    assert stored.startswith("$argon2id$v=19$m=65536,t=3,p=4$")


def test_the_right_password_verifies_and_a_wrong_one_does_not() -> None:
    stored = hash_password(PASSWORD)

    assert verify_password(stored, PASSWORD)
    assert not verify_password(stored, PASSWORD + "!")


def test_the_same_password_gets_a_different_hash_each_time() -> None:
    # A random salt per hash: two users with one password do not share a hash, and a
    # precomputed table of hashes is useless.
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_a_malformed_stored_hash_fails_verification_instead_of_raising() -> None:
    assert not verify_password("not-a-real-hash", PASSWORD)


# ---------------------------------------------------------------- access tokens


def decode_unverified(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


def test_an_access_token_carries_the_user_id_and_lives_fifteen_minutes(settings: Settings) -> None:
    user_id = uuid.uuid4()
    now = datetime.now(UTC).replace(microsecond=0)

    token = create_access_token(user_id, settings, now=now)
    claims = decode_access_token(token, settings)

    assert claims.user_id == user_id
    assert claims.expires_at - claims.issued_at == timedelta(minutes=15)
    assert claims.issued_at == now


def test_an_access_token_does_not_carry_the_role(settings: Settings) -> None:
    # The role is read from the database on every request, so a demoted admin loses access
    # at once instead of when the token expires.
    payload = decode_unverified(create_access_token(uuid.uuid4(), settings))

    assert set(payload) == {"sub", "iss", "iat", "exp", "jti"}


def test_an_expired_access_token_is_rejected(settings: Settings) -> None:
    sixteen_minutes_ago = datetime.now(UTC) - timedelta(minutes=16)
    token = create_access_token(uuid.uuid4(), settings, now=sixteen_minutes_ago)

    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(token, settings)


def test_a_tampered_access_token_is_rejected(settings: Settings) -> None:
    header, payload, signature = create_access_token(uuid.uuid4(), settings).split(".")
    other = create_access_token(uuid.uuid4(), settings).split(".")[1]

    # Someone else's payload under this token's signature.
    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(f"{header}.{other}.{signature}", settings)


def test_a_token_signed_with_another_key_is_rejected(settings: Settings) -> None:
    attacker = Settings(_env_file=None, jwt_secret_key="x" * 40)
    token = create_access_token(uuid.uuid4(), attacker)

    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(token, settings)


def test_an_unsigned_token_is_rejected(settings: Settings) -> None:
    # alg "none": a token with no signature at all. Decoding must insist on HS256.
    payload = decode_unverified(create_access_token(uuid.uuid4(), settings))
    token = jwt.encode(payload, key=None, algorithm="none")

    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(token, settings)


def test_a_token_from_another_issuer_is_rejected(settings: Settings) -> None:
    other_app = Settings(_env_file=None, jwt_issuer="someone-else")
    token = create_access_token(uuid.uuid4(), other_app)

    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(token, settings)


def test_a_token_whose_subject_is_not_a_user_id_is_rejected(settings: Settings) -> None:
    payload = decode_unverified(create_access_token(uuid.uuid4(), settings))
    payload["sub"] = "admin"
    key = settings.jwt_secret_key.get_secret_value()
    token = jwt.encode(payload, key, algorithm="HS256")

    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(token, settings)


def test_garbage_is_rejected(settings: Settings) -> None:
    with pytest.raises(InvalidAccessTokenError):
        decode_access_token("not.a.token", settings)


# ---------------------------------------------------------------- opaque tokens


def test_opaque_tokens_are_long_random_and_url_safe() -> None:
    tokens = {new_opaque_token() for _ in range(100)}

    assert len(tokens) == 100
    for token in tokens:
        assert len(token) >= 43  # 32 random bytes in base64url
        assert set(token) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def test_an_opaque_token_is_stored_as_its_sha256() -> None:
    token = new_opaque_token()

    stored = hash_opaque_token(token)

    assert stored == hash_opaque_token(token)  # same token, same hash: we can look it up
    assert len(stored) == 64 and token not in stored


# ---------------------------------------------------------------- settings


def test_staging_and_production_refuse_the_built_in_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(_env_file=None, environment="production")


def test_a_short_jwt_secret_is_refused() -> None:
    with pytest.raises(ValidationError, match="32"):
        Settings(_env_file=None, jwt_secret_key="too-short")


def test_production_accepts_its_own_secret() -> None:
    settings = Settings(_env_file=None, environment="production", jwt_secret_key="s" * 48)

    assert settings.jwt_secret_key.get_secret_value() == "s" * 48
