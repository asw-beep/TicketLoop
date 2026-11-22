"""POST /auth/register and POST /auth/login (FR-01)."""

import logging

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.security import hash_opaque_token, verify_password
from app.modules.identity.models import RefreshToken, User
from tests.integration.api import PASSWORD, login, new_email, register
from tests.integration.factories import make_user

pytestmark = pytest.mark.integration

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"


def user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == email))


# ---------------------------------------------------------------- register


def test_register_creates_an_active_user_and_returns_it_without_the_hash(
    api: TestClient,
) -> None:
    email = new_email()

    body = register(api, email=email)

    assert set(body) == {"id", "email", "full_name", "role", "account_status", "created_at"}
    assert (body["email"], body["role"], body["account_status"]) == (email, "USER", "ACTIVE")


def test_register_stores_an_argon2id_hash_never_the_password(
    api: TestClient, db_session: Session
) -> None:
    email = new_email()
    register(api, email=email)

    user = user_by_email(db_session, email)

    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash
    assert verify_password(user.password_hash, PASSWORD)


def test_register_stores_the_email_lowercased_and_trimmed(
    api: TestClient, db_session: Session
) -> None:
    body = register(api, email="  Asha.Menon@Example.IN ")

    assert body["email"] == "asha.menon@example.in"
    assert user_by_email(db_session, "asha.menon@example.in") is not None


def test_an_email_already_taken_in_any_case_gives_409(api: TestClient) -> None:
    register(api, email="taken@example.in")

    response = api.post(
        REGISTER, json={"email": "TAKEN@example.in", "password": PASSWORD, "full_name": "Other"}
    )

    assert response.status_code == 409
    assert response.json()["type"] == "urn:ticketloop:problem:email-taken"


@pytest.mark.parametrize(
    "change",
    [
        {"password": "x" * 11},
        {"password": "x" * 129},
        {"email": "not-an-email"},
        {"full_name": ""},
        {"full_name": "x" * 201},
        {"role": "ADMIN"},  # unknown fields are rejected, so nobody can make themselves admin
    ],
    ids=["password too short", "password too long", "bad email", "empty name", "long name", "role"],
)
def test_an_invalid_registration_gives_422(api: TestClient, change: dict[str, str]) -> None:
    body = {"email": new_email(), "password": PASSWORD, "full_name": "Asha"} | change

    response = api.post(REGISTER, json=body)

    assert response.status_code == 422


def test_a_rejected_password_is_not_echoed_back(api: TestClient) -> None:
    secret = "tiny-secret"  # 11 characters, one under the minimum
    response = api.post(REGISTER, json={"email": new_email(), "password": secret, "full_name": "A"})

    assert response.status_code == 422
    assert secret not in response.text


# ---------------------------------------------------------------- login


def test_login_returns_a_token_pair(api: TestClient) -> None:
    user = register(api)

    pair = login(api, user["email"])

    assert set(pair) == {"access_token", "refresh_token", "token_type", "expires_in"}
    assert (pair["token_type"], pair["expires_in"]) == ("bearer", 900)


def test_login_ignores_the_case_of_the_email(api: TestClient) -> None:
    user = register(api)

    login(api, user["email"].upper())


def test_login_stores_only_a_hash_of_the_refresh_token_in_a_new_family(
    api: TestClient, db_session: Session
) -> None:
    user = register(api)
    first = login(api, user["email"])
    second = login(api, user["email"])

    rows = {
        row.token_hash: row
        for row in db_session.scalars(
            select(RefreshToken).where(RefreshToken.user_id == user["id"])
        )
    }

    assert set(rows) == {
        hash_opaque_token(first["refresh_token"]),
        hash_opaque_token(second["refresh_token"]),
    }
    one, two = rows.values()
    assert one.family_id != two.family_id  # each login is its own session
    assert one.revoked_at is None and two.revoked_at is None


def test_a_wrong_password_and_an_unknown_email_get_the_same_401(api: TestClient) -> None:
    user = register(api)

    wrong_password = api.post(LOGIN, json={"email": user["email"], "password": "x" * 20})
    unknown_email = api.post(LOGIN, json={"email": new_email(), "password": PASSWORD})

    assert wrong_password.status_code == unknown_email.status_code == 401

    def without_request_fields(body: dict) -> dict:
        return {k: v for k, v in body.items() if k not in ("request_id",)}

    assert without_request_fields(wrong_password.json()) == without_request_fields(
        unknown_email.json()
    )
    assert wrong_password.headers["WWW-Authenticate"] == "Bearer"


def test_a_suspended_user_with_the_right_password_gets_403(
    api: TestClient, db_session: Session
) -> None:
    user = register(api)
    db_session.get(User, user["id"]).account_status = "SUSPENDED"
    db_session.flush()

    response = api.post(LOGIN, json={"email": user["email"], "password": PASSWORD})

    assert response.status_code == 403
    assert response.json()["type"] == "urn:ticketloop:problem:account-suspended"


def test_a_suspended_user_with_a_wrong_password_learns_nothing_more(
    api: TestClient, db_session: Session
) -> None:
    make_user(db_session, email="suspended@example.in", account_status="SUSPENDED")

    response = api.post(LOGIN, json={"email": "suspended@example.in", "password": "x" * 20})

    assert response.status_code == 401


def test_login_upgrades_a_hash_made_with_weaker_settings(
    api: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Raising the cost later must not force every user to reset their password: the next
    # successful login, the one moment we hold the plaintext, re-hashes it.
    old = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    current = PasswordHasher(time_cost=2, memory_cost=16, parallelism=1)
    monkeypatch.setattr(security, "_hasher", current)
    user = make_user(db_session, password_hash=old.hash(PASSWORD))
    assert current.check_needs_rehash(user.password_hash)

    login(api, user.email)
    db_session.refresh(user)

    assert not current.check_needs_rehash(user.password_hash)
    assert verify_password(user.password_hash, PASSWORD)


def test_neither_register_nor_login_writes_the_password_to_any_log(
    api: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    user = register(api)
    login(api, user["email"])
    api.post(LOGIN, json={"email": user["email"], "password": "wrong password but long"})

    assert caplog.records  # the access log did run
    assert PASSWORD not in caplog.text
    assert "wrong password but long" not in caplog.text
