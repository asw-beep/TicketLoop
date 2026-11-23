"""FR-12: every admin route refuses a regular user.

The routes are collected from the app itself, so an admin route added later is checked here
without anyone remembering to list it.
"""

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.integration.api import bearer

pytestmark = pytest.mark.integration

ANY_ID = "00000000-0000-4000-8000-000000000000"


def admin_routes(app: FastAPI) -> set[tuple[str, str]]:
    # Read from the OpenAPI schema, as test_ticket_access.py does: included routes are not
    # listed flat in app.routes.
    return {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/v1/admin")
        for method in operations
    }


def test_the_guard_sees_the_m5_routes(db_app: FastAPI) -> None:
    paths = {path for _, path in admin_routes(db_app)}

    assert "/api/v1/admin/listings" in paths
    assert "/api/v1/admin/listings/{listing_id}/approve" in paths


def test_every_admin_route_refuses_a_user(
    db_app: FastAPI, api: TestClient, user: dict[str, str]
) -> None:
    allowed = []
    for method, path in sorted(admin_routes(db_app)):
        url = re.sub(r"\{[^}]+\}", ANY_ID, path)
        # The admin check runs before the body and path are validated, so 403 wins.
        response = api.request(method, url, json={}, headers=bearer(user["token"]))
        if response.status_code != 403:
            allowed.append((method, path, response.status_code))

    assert allowed == []
