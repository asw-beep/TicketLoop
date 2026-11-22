"""Small helpers for calling the auth API from tests, so each test reads as what it checks."""

import uuid
from typing import Any

from fastapi.testclient import TestClient

PASSWORD = "correct horse battery staple"


def new_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.in"


def register(client: TestClient, email: str | None = None, **fields: Any) -> dict[str, Any]:
    body = {"email": email or new_email(), "password": PASSWORD, "full_name": "Asha Menon"}
    response = client.post("/api/v1/auth/register", json=body | fields)
    assert response.status_code == 201, response.text
    return response.json()


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def registered_and_logged_in(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """A new user and a token pair for them."""
    user = register(client)
    return user, login(client, user["email"])


def bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}
