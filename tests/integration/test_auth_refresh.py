"""POST /auth/refresh and POST /auth/logout: rotation, reuse detection, revocation (FR-01)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_opaque_token, new_opaque_token
from app.modules.identity.models import RefreshToken, User
from tests.integration.api import bearer, login, registered_and_logged_in
from tests.integration.factories import make_refresh_token, make_user

pytestmark = pytest.mark.integration

REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/users/me"


def refresh(api: TestClient, token: str):
    return api.post(REFRESH, json={"refresh_token": token})


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


# ---------------------------------------------------------------- rotation


def test_refresh_returns_a_new_pair_and_revokes_the_old_refresh_token(
    api: TestClient, db_session: Session
) -> None:
    _, first = registered_and_logged_in(api)

    response = refresh(api, first["refresh_token"])

    assert response.status_code == 200
    second = response.json()
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

    second = refresh(api, first["refresh_token"]).json()

    assert api.get(ME, headers=bearer(second["access_token"])).json()["id"] == user["id"]


def test_a_refresh_token_can_be_rotated_many_times(api: TestClient) -> None:
    _, pair = registered_and_logged_in(api)

    for _ in range(5):
        response = refresh(api, pair["refresh_token"])
        assert response.status_code == 200
        pair = response.json()


# ---------------------------------------------------------------- reuse detection


def test_reusing_a_rotated_refresh_token_revokes_the_whole_family(
    api: TestClient, db_session: Session
) -> None:
    user, stolen = registered_and_logged_in(api)
    # The real user refreshes twice; the thief kept the first token.
    latest = refresh(api, stolen["refresh_token"]).json()
    latest = refresh(api, latest["refresh_token"]).json()

    replay = refresh(api, stolen["refresh_token"])

    assert replay.status_code == 401
    assert problem_type(replay) == "invalid-refresh-token"
    assert live_tokens(db_session, user["id"]) == []
    # Neither side can carry on: the real user logs in again, the thief cannot.
    assert refresh(api, latest["refresh_token"]).status_code == 401


def test_reuse_revokes_only_the_family_it_belongs_to(api: TestClient, db_session: Session) -> None:
    user, laptop = registered_and_logged_in(api)
    phone = login(api, user["email"])  # a second login: a second family
    refresh(api, laptop["refresh_token"])

    refresh(api, laptop["refresh_token"])  # replay on the laptop's family

    assert [row.token_hash for row in live_tokens(db_session, user["id"])] == [
        hash_opaque_token(phone["refresh_token"])
    ]
    assert refresh(api, phone["refresh_token"]).status_code == 200


# ---------------------------------------------------------------- refused tokens


def test_an_unknown_refresh_token_gives_401(api: TestClient) -> None:
    response = refresh(api, new_opaque_token())

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_an_expired_refresh_token_gives_401(api: TestClient, db_session: Session) -> None:
    token = new_opaque_token()
    make_refresh_token(
        db_session,
        token_hash=hash_opaque_token(token),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    assert refresh(api, token).status_code == 401


def test_an_access_token_is_not_a_refresh_token(api: TestClient) -> None:
    _, pair = registered_and_logged_in(api)

    assert refresh(api, pair["access_token"]).status_code == 401


def test_a_suspended_user_cannot_refresh(api: TestClient, db_session: Session) -> None:
    user, pair = registered_and_logged_in(api)
    db_session.get(User, user["id"]).account_status = "SUSPENDED"
    db_session.flush()

    response = refresh(api, pair["refresh_token"])

    assert response.status_code == 403
    assert problem_type(response) == "account-suspended"


# ---------------------------------------------------------------- logout


def test_logout_ends_the_session(api: TestClient) -> None:
    _, pair = registered_and_logged_in(api)

    response = api.post(
        LOGOUT, headers=bearer(pair["access_token"]), json={"refresh_token": pair["refresh_token"]}
    )

    assert response.status_code == 204
    assert refresh(api, pair["refresh_token"]).status_code == 401


def test_logout_with_an_older_token_from_the_session_still_ends_it(api: TestClient) -> None:
    _, first = registered_and_logged_in(api)
    latest = refresh(api, first["refresh_token"]).json()

    api.post(
        LOGOUT,
        headers=bearer(latest["access_token"]),
        json={"refresh_token": first["refresh_token"]},
    )

    assert refresh(api, latest["refresh_token"]).status_code == 401


def test_logout_leaves_other_sessions_alone(api: TestClient) -> None:
    user, laptop = registered_and_logged_in(api)
    phone = login(api, user["email"])

    api.post(
        LOGOUT,
        headers=bearer(laptop["access_token"]),
        json={"refresh_token": laptop["refresh_token"]},
    )

    assert refresh(api, phone["refresh_token"]).status_code == 200


def test_logging_out_twice_is_fine(api: TestClient) -> None:
    _, pair = registered_and_logged_in(api)
    auth, body = bearer(pair["access_token"]), {"refresh_token": pair["refresh_token"]}

    api.post(LOGOUT, headers=auth, json=body)

    assert api.post(LOGOUT, headers=auth, json=body).status_code == 204


def test_logout_needs_an_access_token(api: TestClient) -> None:
    _, pair = registered_and_logged_in(api)

    response = api.post(LOGOUT, json={"refresh_token": pair["refresh_token"]})

    assert response.status_code == 401
    assert refresh(api, pair["refresh_token"]).status_code == 200


def test_nobody_can_log_out_someone_else(api: TestClient, db_session: Session) -> None:
    _, mine = registered_and_logged_in(api)
    victim = make_user(db_session)
    their_token = new_opaque_token()
    make_refresh_token(db_session, user_id=victim.id, token_hash=hash_opaque_token(their_token))

    response = api.post(
        LOGOUT, headers=bearer(mine["access_token"]), json={"refresh_token": their_token}
    )

    assert response.status_code == 401
    assert row_for(db_session, their_token).revoked_at is None
