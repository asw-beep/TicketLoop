"""get_current_user and require_admin, through GET and PATCH /users/me and a test-only admin route.

Every protected route gets these checks from the two dependencies, so testing them here once
covers every route that uses them.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.modules.identity.dependencies import AdminUser
from app.modules.identity.models import User
from tests.integration.api import bearer, registered_and_logged_in
from tests.integration.factories import make_user

pytestmark = pytest.mark.integration

ME = "/api/v1/users/me"

admin_only = APIRouter()


@admin_only.get("/api/v1/admin/test-only")
def an_admin_route(admin: AdminUser) -> dict[str, str]:
    return {"admin": admin.email}


@pytest.fixture
def api(db_app: FastAPI) -> Iterator[TestClient]:
    db_app.include_router(admin_only)
    with TestClient(db_app) as client:
        yield client


def problem_type(response) -> str:
    return response.json()["type"].removeprefix("urn:ticketloop:problem:")


# ---------------------------------------------------------------- who are you? (401)


def test_a_valid_access_token_identifies_the_user(api: TestClient) -> None:
    user, tokens = registered_and_logged_in(api)

    response = api.get(ME, headers=bearer(tokens["access_token"]))

    assert response.status_code == 200
    assert response.json() == user


def test_no_token_gives_401_naming_the_bearer_scheme(api: TestClient) -> None:
    response = api.get(ME)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert problem_type(response) == "not-authenticated"


@pytest.mark.parametrize(
    "header",
    ["Bearer", "Bearer not-a-jwt", "Basic dXNlcjpwYXNz", "eyJhbGciOiJIUzI1NiJ9.e30.x"],
    ids=["empty bearer", "garbage", "wrong scheme", "no scheme"],
)
def test_a_malformed_authorization_header_gives_401(api: TestClient, header: str) -> None:
    response = api.get(ME, headers={"Authorization": header})

    assert response.status_code == 401


def test_an_expired_access_token_gives_401(api: TestClient, db_app: FastAPI) -> None:
    user, _ = registered_and_logged_in(api)
    issued = datetime.now(UTC) - timedelta(minutes=16)
    expired = create_access_token(uuid.UUID(user["id"]), db_app.state.settings, now=issued)

    response = api.get(ME, headers=bearer(expired))

    assert response.status_code == 401


def test_a_tampered_access_token_gives_401(api: TestClient) -> None:
    _, mine = registered_and_logged_in(api)
    _, theirs = registered_and_logged_in(api)
    header, _, signature = mine["access_token"].split(".")
    their_payload = theirs["access_token"].split(".")[1]

    response = api.get(ME, headers=bearer(f"{header}.{their_payload}.{signature}"))

    assert response.status_code == 401


def test_a_valid_token_for_a_user_that_does_not_exist_gives_401(
    api: TestClient, db_app: FastAPI
) -> None:
    token = create_access_token(uuid.uuid4(), db_app.state.settings)

    response = api.get(ME, headers=bearer(token))

    assert response.status_code == 401


# ---------------------------------------------------------------- may you? (403)


def test_a_suspended_user_cannot_use_an_access_token_issued_before_suspension(
    api: TestClient, db_session: Session
) -> None:
    user, tokens = registered_and_logged_in(api)
    db_session.get(User, user["id"]).account_status = "SUSPENDED"
    db_session.flush()

    response = api.get(ME, headers=bearer(tokens["access_token"]))

    assert response.status_code == 403
    assert problem_type(response) == "account-suspended"


def test_a_normal_user_calling_an_admin_route_gets_403(api: TestClient) -> None:
    _, tokens = registered_and_logged_in(api)

    response = api.get("/api/v1/admin/test-only", headers=bearer(tokens["access_token"]))

    assert response.status_code == 403
    assert problem_type(response) == "admin-required"


def test_an_admin_can_call_an_admin_route(
    api: TestClient, db_app: FastAPI, db_session: Session
) -> None:
    admin = make_user(db_session, role="ADMIN")
    token = create_access_token(admin.id, db_app.state.settings)

    response = api.get("/api/v1/admin/test-only", headers=bearer(token))

    assert response.status_code == 200
    assert response.json() == {"admin": admin.email}


def test_an_admin_route_without_a_token_gives_401_not_403(api: TestClient) -> None:
    assert api.get("/api/v1/admin/test-only").status_code == 401


def test_a_demoted_admin_loses_admin_access_at_once(
    api: TestClient, db_app: FastAPI, db_session: Session
) -> None:
    # The role is read from the database on each request, not trusted from the token.
    admin = make_user(db_session, role="ADMIN")
    token = create_access_token(admin.id, db_app.state.settings)
    admin.role = "USER"
    db_session.flush()

    response = api.get("/api/v1/admin/test-only", headers=bearer(token))

    assert response.status_code == 403


# ---------------------------------------------------------------- PATCH /users/me


def test_a_user_can_change_their_name(api: TestClient) -> None:
    _, tokens = registered_and_logged_in(api)
    auth = bearer(tokens["access_token"])

    response = api.patch(ME, headers=auth, json={"full_name": "  Asha M.  "})

    assert response.status_code == 200
    assert response.json()["full_name"] == "Asha M."
    assert api.get(ME, headers=auth).json()["full_name"] == "Asha M."


@pytest.mark.parametrize(
    "body",
    [{"full_name": ""}, {"email": "new@example.in"}, {"role": "ADMIN"}],
    ids=["empty name", "email", "role"],
)
def test_patch_me_changes_only_the_name(api: TestClient, body: dict[str, str]) -> None:
    _, tokens = registered_and_logged_in(api)

    response = api.patch(ME, headers=bearer(tokens["access_token"]), json=body)

    assert response.status_code == 422


def test_patch_me_needs_a_token(api: TestClient) -> None:
    assert api.patch(ME, json={"full_name": "Nobody"}).status_code == 401
