"""Ticket providers: admins list and add them; one is seeded by the migration."""

import pytest
from fastapi.testclient import TestClient

from tests.integration.api import bearer
from tests.integration.conftest import new_account

pytestmark = pytest.mark.integration

PROVIDERS = "/api/v1/admin/providers"


def test_the_list_includes_the_seeded_provider(api: TestClient, admin: dict[str, str]) -> None:
    response = api.get(PROVIDERS, headers=bearer(admin["token"]))

    assert response.status_code == 200
    assert {"code": "fakeprovider", "name": "FakeProvider"}.items() <= next(
        p for p in response.json() if p["code"] == "fakeprovider"
    ).items()


def test_an_admin_adds_a_provider(api: TestClient, admin: dict[str, str]) -> None:
    response = api.post(
        PROVIDERS, json={"code": "bookmyshow", "name": "BookMyShow"}, headers=bearer(admin["token"])
    )

    assert response.status_code == 201, response.text
    assert response.json()["code"] == "bookmyshow"


def test_a_duplicate_code_is_409(api: TestClient, admin: dict[str, str]) -> None:
    body = {"code": "fakeprovider", "name": "Again"}
    response = api.post(PROVIDERS, json=body, headers=bearer(admin["token"]))

    assert response.status_code == 409
    assert response.json()["type"].endswith("provider-code-taken")


@pytest.mark.parametrize("code", ["A", "Has Spaces", "UPPER", "x" * 51])
def test_a_malformed_code_is_422(api: TestClient, admin: dict[str, str], code: str) -> None:
    response = api.post(
        PROVIDERS, json={"code": code, "name": "Name"}, headers=bearer(admin["token"])
    )

    assert response.status_code == 422


def test_a_user_cannot_add_a_provider(api: TestClient) -> None:
    user = new_account(api)
    response = api.post(
        PROVIDERS, json={"code": "mine", "name": "Mine"}, headers=bearer(user["token"])
    )

    assert response.status_code == 403
