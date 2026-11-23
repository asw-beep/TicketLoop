"""get_optional_user, through a test-only route: who is calling, when signing in is optional.

No header means a guest. A header with a bad token is still a 401, so a client whose token
expired refreshes it instead of silently being shown the guest's view.
"""

from collections.abc import Iterator

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.modules.identity.dependencies import OptionalUser
from tests.integration.api import bearer, problem_code, registered_and_logged_in

pytestmark = pytest.mark.integration

PROBE = "/api/v1/test-only/whoami"

probe = APIRouter()


@probe.get(PROBE)
def whoami(user: OptionalUser) -> dict[str, str | None]:
    return {"email": user.email if user else None}


@pytest.fixture
def api(db_app: FastAPI) -> Iterator[TestClient]:
    db_app.include_router(probe)
    with TestClient(db_app) as client:
        yield client


def test_no_header_is_a_guest(api: TestClient) -> None:
    response = api.get(PROBE)

    assert response.status_code == 200
    assert response.json() == {"email": None}


def test_a_valid_token_identifies_the_user(api: TestClient) -> None:
    user, tokens = registered_and_logged_in(api)

    response = api.get(PROBE, headers=bearer(tokens["access_token"]))

    assert response.json() == {"email": user["email"]}


def test_a_bad_token_is_401_not_a_guest(api: TestClient) -> None:
    response = api.get(PROBE, headers=bearer("not-a-token"))

    assert response.status_code == 401
    assert problem_code(response) == "not-authenticated"
