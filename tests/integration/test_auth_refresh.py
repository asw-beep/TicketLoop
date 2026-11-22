"""POST /auth/refresh and POST /auth/logout: the cookie, rotation, reuse detection (FR-01)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_opaque_token, new_opaque_token
from app.modules.identity.models import RefreshToken, User
from tests.integration.api import (
    PASSWORD,
    bearer,
    cookie_header,
    login,
    refreshed,
    registered_and_logged_in,
    send_refresh,
)
from tests.integration.factories import make_refresh_token

pytestmark = pytest.mark.integration

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/users/me"


def row_for(session: Session, token: str) -> RefreshToken:
    session.expire_all()  # read what the API committed, not a stale copy
    return session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_opaque_token(token))
    )


def live_tokens(session: Session, user_id: str) -> list[RefreshToken]:
    session.expire_all()
    return list(
        session.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
            )
        )
    )


def problem_type(response) -> str:
    return response.json()["type"].removeprefix("urn:ticketloop:problem:")


def cookie_attributes(response) -> dict[str, str]:
    """The Set-Cookie header as {attribute: value}, names lowercased."""
    name_value, *attributes = response.headers["set-cookie"].split(";")
    name, _, value = name_value.partition("=")
    parsed = {"name": name.strip(), "value": value.strip().strip('"')}
    for attribute in attributes:
        key, _, val = attribute.strip().partition("=")
        parsed[key.lower()] = val
    return parsed


# ---------------------------------------------------------------- the cookie


def test_login_puts_the_refresh_token_in_an_httponly_strict_cookie_for_auth_routes_only(
    api: TestClient,
) -> None:
    user, _ = registered_and_logged_in(api)

    response = api.post(LOGIN, json={"email": user["email"], "password": PASSWORD})

    cookie = cookie_attributes(response)
    assert cookie["name"] == "refresh_token" and len(cookie["value"]) == 43
    assert "httponly" in cookie  # page scripts cannot read it
    assert cookie["samesite"].lower() == "strict"  # other sites cannot make the browser send it
    assert cookie["path"] == "/api/v1/auth"  # sent to refresh and logout, nowhere else
    assert cookie["max-age"] == str(7 * 24 * 3600)
    assert "secure" not in cookie  # plain http in tests; staging and production add Secure


def test_the_refresh_token_never_appears_in_a_response_body(api: TestClient) -> None:
    # If any body carried it, a script injected into the page could call the endpoint and
    # read the token, and the HttpOnly cookie would protect nothing.
    _, tokens = registered_and_logged_in(api)

    response = send_refresh(api, tokens["refresh_token"])

    assert set(response.json()) == {"access_token", "token_type", "expires_in"}


def test_a_browser_keeps_its_session_with_the_cookie_alone(api: TestClient) -> None:
    # No helper, no explicit header: the client stores and sends the cookie like a browser.
    user, _ = registered_and_logged_in(api)
    api.post(LOGIN, json={"email": user["email"], "password": PASSWORD})

    first = api.post(REFRESH)
    second = api.post(REFRESH)  # uses the rotated cookie from the first response

    assert (first.status_code, second.status_code) == (200, 200)
    assert api.get(ME, headers=bearer(second.json()["access_token"])).json()["id"] == user["id"]


def test_refresh_without_a_cookie_gives_401(api: TestClient) -> None:
    response = api.post(REFRESH)

    assert response.status_code == 401
    assert problem_type(response) == "invalid-refresh-token"


# ---------------------------------------------------------------- rotation


def test_refresh_returns_a_new_access_token_and_rotates_the_cookie(
    api: TestClient, db_session: Session
) -> None:
    _, first = registered_and_logged_in(api)

    second = refreshed(api, first["refresh_token"])

    assert second["refresh_token"] != first["refresh_token"]
    assert second["access_token"] != first["access_token"]
    old, new = (
        row_for(db_session, first["refresh_token"]),
        row_for(db_session, second["refresh_token"]),
    )
    assert old.revoked_at is not None
    assert old.replaced_by == new.id  # the chain can be followed token by token
    assert new.family_id == old.family_id and new.revoked_at is None


def test_the_new_access_token_works(api: TestClient) -> None:
    user, first = registered_and_logged_in(api)

    second = refreshed(api, first["refresh_token"])

    assert api.get(ME, headers=bearer(second["access_token"])).json()["id"] == user["id"]


def test_a_refresh_token_can_be_rotated_many_times(api: TestClient) -> None:
    _, tokens = registered_and_logged_in(api)

    for _ in range(5):
        tokens = refreshed(api, tokens["refresh_token"])


# ---------------------------------------------------------------- reuse detection


def test_reusing_a_rotated_refresh_token_revokes_the_whole_family(
    api: TestClient, db_session: Session
) -> None:
    user, stolen = registered_and_logged_in(api)
    # The real user refreshes twice; the thief kept the first token.
    latest = refreshed(api, stolen["refresh_token"])
    latest = refreshed(api, latest["refresh_token"])

    replay = send_refresh(api, stolen["refresh_token"])

    assert replay.status_code == 401
    assert problem_type(replay) == "invalid-refresh-token"
    assert live_tokens(db_session, user["id"]) == []
    # Neither side can carry on: the real user logs in again, the thief cannot.
    assert send_refresh(api, latest["refresh_token"]).status_code == 401


def test_reuse_revokes_only_the_family_it_belongs_to(api: TestClient, db_session: Session) -> None:
    user, laptop = registered_and_logged_in(api)
    phone = login(api, user["email"])  # a second login: a second family
    refreshed(api, laptop["refresh_token"])

    send_refresh(api, laptop["refresh_token"])  # replay on the laptop's family

    assert [row.token_hash for row in live_tokens(db_session, user["id"])] == [
        hash_opaque_token(phone["refresh_token"])
    ]
    assert send_refresh(api, phone["refresh_token"]).status_code == 200


# ---------------------------------------------------------------- refused tokens


def test_an_unknown_refresh_token_gives_401(api: TestClient) -> None:
    response = send_refresh(api, new_opaque_token())

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_an_expired_refresh_token_gives_401(api: TestClient, db_session: Session) -> None:
    token = new_opaque_token()
    make_refresh_token(
        db_session,
        token_hash=hash_opaque_token(token),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    assert send_refresh(api, token).status_code == 401


def test_an_access_token_is_not_a_refresh_token(api: TestClient) -> None:
    _, tokens = registered_and_logged_in(api)

    assert send_refresh(api, tokens["access_token"]).status_code == 401


def test_a_suspended_user_cannot_refresh(api: TestClient, db_session: Session) -> None:
    user, tokens = registered_and_logged_in(api)
    db_session.get(User, user["id"]).account_status = "SUSPENDED"
    db_session.flush()

    response = send_refresh(api, tokens["refresh_token"])

    assert response.status_code == 403
    assert problem_type(response) == "account-suspended"


# ---------------------------------------------------------------- logout


def logout(api: TestClient, token: str):
    return api.post(LOGOUT, headers=cookie_header(token))


def test_logout_ends_the_session_and_clears_the_cookie(api: TestClient) -> None:
    _, tokens = registered_and_logged_in(api)

    response = logout(api, tokens["refresh_token"])

    assert response.status_code == 204
    cleared = cookie_attributes(response)
    assert (cleared["name"], cleared["value"], cleared["max-age"]) == ("refresh_token", "", "0")
    assert cleared["path"] == "/api/v1/auth"  # the same path, or the browser keeps the old one
    assert send_refresh(api, tokens["refresh_token"]).status_code == 401


def test_logout_with_an_older_token_from_the_session_still_ends_it(api: TestClient) -> None:
    _, first = registered_and_logged_in(api)
    latest = refreshed(api, first["refresh_token"])

    logout(api, first["refresh_token"])

    assert send_refresh(api, latest["refresh_token"]).status_code == 401


def test_logout_leaves_other_sessions_alone(api: TestClient) -> None:
    user, laptop = registered_and_logged_in(api)
    phone = login(api, user["email"])

    logout(api, laptop["refresh_token"])

    assert send_refresh(api, phone["refresh_token"]).status_code == 200


def test_logging_out_twice_is_fine(api: TestClient) -> None:
    _, tokens = registered_and_logged_in(api)
    logout(api, tokens["refresh_token"])

    assert logout(api, tokens["refresh_token"]).status_code == 204


def test_logout_without_a_cookie_is_fine(api: TestClient) -> None:
    # Nothing to end. The page may still be showing a session whose cookie already expired.
    assert api.post(LOGOUT).status_code == 204


def test_logout_needs_no_access_token(api: TestClient) -> None:
    # The cookie is the session. Asking for an access token too would stop someone whose
    # access token just expired from logging out.
    _, tokens = registered_and_logged_in(api)

    response = logout(api, tokens["refresh_token"])

    assert response.status_code == 204
    assert send_refresh(api, tokens["refresh_token"]).status_code == 401
