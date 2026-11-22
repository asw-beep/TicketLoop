"""Changing a password, and recovering an account with a reset token (FR-01)."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_opaque_token, new_opaque_token
from app.infrastructure.external_providers.email import InMemoryEmailSender
from app.modules.identity.models import PasswordResetToken, RefreshToken
from tests.integration.api import PASSWORD, bearer, login, register, registered_and_logged_in
from tests.integration.factories import make_password_reset_token, make_user

pytestmark = pytest.mark.integration

CHANGE = "/api/v1/users/me/password"
RESET_REQUEST = "/api/v1/auth/password-reset/request"
RESET_CONFIRM = "/api/v1/auth/password-reset/confirm"
LOGIN = "/api/v1/auth/login"
NEW_PASSWORD = "a brand new passphrase"


@pytest.fixture
def mailbox(db_app: FastAPI) -> InMemoryEmailSender:
    return db_app.state.email_sender


def live_refresh_tokens(session: Session, user_id: str) -> int:
    session.expire_all()
    rows = session.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
    )
    return len(rows.all())


def can_log_in(api: TestClient, email: str, password: str) -> bool:
    return api.post(LOGIN, json={"email": email, "password": password}).status_code == 200


def token_in(message_body: str) -> str:
    match = re.search(r"[A-Za-z0-9_-]{43}", message_body)
    assert match, message_body
    return match.group()


# ---------------------------------------------------------------- change password


def test_changing_the_password_replaces_it_and_ends_every_session(
    api: TestClient, db_session: Session
) -> None:
    user, laptop = registered_and_logged_in(api)
    login(api, user["email"])  # a phone session too

    response = api.post(
        CHANGE,
        headers=bearer(laptop["access_token"]),
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 204
    assert not can_log_in(api, user["email"], PASSWORD)
    assert live_refresh_tokens(db_session, user["id"]) == 0  # checked before the next login
    assert can_log_in(api, user["email"], NEW_PASSWORD)


def test_a_wrong_current_password_gives_401_and_changes_nothing(
    api: TestClient, db_session: Session
) -> None:
    user, pair = registered_and_logged_in(api)

    response = api.post(
        CHANGE,
        headers=bearer(pair["access_token"]),
        json={"current_password": "not my password", "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 401
    assert response.json()["type"] == "urn:ticketloop:problem:wrong-password"
    assert can_log_in(api, user["email"], PASSWORD)
    assert live_refresh_tokens(db_session, user["id"]) == 2  # the original and the check above


def test_a_new_password_must_meet_the_length_rule(api: TestClient) -> None:
    _, pair = registered_and_logged_in(api)

    response = api.post(
        CHANGE,
        headers=bearer(pair["access_token"]),
        json={"current_password": PASSWORD, "new_password": "short"},
    )

    assert response.status_code == 422


def test_changing_the_password_needs_an_access_token(api: TestClient) -> None:
    response = api.post(CHANGE, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})

    assert response.status_code == 401


# ---------------------------------------------------------------- reset: request


def test_a_reset_request_emails_a_token_and_stores_only_its_hash(
    api: TestClient, db_session: Session, mailbox: InMemoryEmailSender
) -> None:
    user = register(api)

    response = api.post(RESET_REQUEST, json={"email": user["email"]})

    assert response.status_code == 202
    [message] = mailbox.sent
    assert message.to == user["email"]
    token = token_in(message.body)
    row = db_session.scalar(
        select(PasswordResetToken).where(PasswordResetToken.user_id == user["id"])
    )
    assert row.token_hash == hash_opaque_token(token)
    assert row.expires_at - row.created_at == pytest.approx(
        timedelta(minutes=30), abs=timedelta(seconds=5)
    )


def test_a_reset_request_for_an_unknown_email_looks_exactly_the_same(
    api: TestClient, mailbox: InMemoryEmailSender
) -> None:
    user = register(api)
    known = api.post(RESET_REQUEST, json={"email": user["email"]})

    unknown = api.post(RESET_REQUEST, json={"email": "nobody@example.in"})

    assert (unknown.status_code, unknown.json()) == (known.status_code, known.json())
    assert [message.to for message in mailbox.sent] == [user["email"]]


def test_a_suspended_account_gets_no_reset_email(
    api: TestClient, db_session: Session, mailbox: InMemoryEmailSender
) -> None:
    make_user(db_session, email="suspended@example.in", account_status="SUSPENDED")

    response = api.post(RESET_REQUEST, json={"email": "suspended@example.in"})

    assert response.status_code == 202
    assert mailbox.sent == []


# ---------------------------------------------------------------- reset: confirm


def requested_reset_token(api: TestClient, mailbox: InMemoryEmailSender, email: str) -> str:
    api.post(RESET_REQUEST, json={"email": email})
    return token_in(mailbox.sent[-1].body)


def test_a_reset_token_sets_a_new_password_and_ends_every_session(
    api: TestClient, db_session: Session, mailbox: InMemoryEmailSender
) -> None:
    user, _ = registered_and_logged_in(api)
    token = requested_reset_token(api, mailbox, user["email"])

    response = api.post(RESET_CONFIRM, json={"token": token, "new_password": NEW_PASSWORD})

    assert response.status_code == 204
    assert live_refresh_tokens(db_session, user["id"]) == 0
    assert not can_log_in(api, user["email"], PASSWORD)
    assert can_log_in(api, user["email"], NEW_PASSWORD)


def test_a_reset_token_works_only_once(api: TestClient, mailbox: InMemoryEmailSender) -> None:
    user = register(api)
    token = requested_reset_token(api, mailbox, user["email"])
    api.post(RESET_CONFIRM, json={"token": token, "new_password": NEW_PASSWORD})

    again = api.post(RESET_CONFIRM, json={"token": token, "new_password": "and another one!"})

    assert again.status_code == 400
    assert again.json()["type"] == "urn:ticketloop:problem:invalid-reset-token"
    assert can_log_in(api, user["email"], NEW_PASSWORD)


def test_using_one_reset_link_cancels_the_others(
    api: TestClient, mailbox: InMemoryEmailSender
) -> None:
    user = register(api)
    older = requested_reset_token(api, mailbox, user["email"])
    newer = requested_reset_token(api, mailbox, user["email"])
    api.post(RESET_CONFIRM, json={"token": newer, "new_password": NEW_PASSWORD})

    response = api.post(RESET_CONFIRM, json={"token": older, "new_password": "and another one!"})

    assert response.status_code == 400


def test_an_expired_reset_token_gives_400(api: TestClient, db_session: Session) -> None:
    token = new_opaque_token()
    make_password_reset_token(
        db_session,
        token_hash=hash_opaque_token(token),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    response = api.post(RESET_CONFIRM, json={"token": token, "new_password": NEW_PASSWORD})

    assert response.status_code == 400


def test_an_unknown_reset_token_gives_400(api: TestClient) -> None:
    response = api.post(
        RESET_CONFIRM, json={"token": new_opaque_token(), "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 400


def test_a_reset_needs_a_valid_new_password(api: TestClient, mailbox: InMemoryEmailSender) -> None:
    user = register(api)
    token = requested_reset_token(api, mailbox, user["email"])

    response = api.post(RESET_CONFIRM, json={"token": token, "new_password": "short"})

    assert response.status_code == 422
    # The token was not spent on a request that failed validation.
    ok = api.post(RESET_CONFIRM, json={"token": token, "new_password": NEW_PASSWORD})
    assert ok.status_code == 204
