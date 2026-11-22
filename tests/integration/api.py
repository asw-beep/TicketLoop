"""Small helpers for calling the auth API from tests, so each test reads as what it checks.

The refresh token travels only in an HttpOnly cookie. The client keeps cookies like a browser
does, but tests that replay an old token need to choose which token goes out, so the helpers
below send it explicitly in a Cookie header and leave the client's own cookie jar empty.
"""

import uuid
from typing import Any

from fastapi.testclient import TestClient

PASSWORD = "correct horse battery staple"
REFRESH_COOKIE = "refresh_token"


def new_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.in"


def register(client: TestClient, email: str | None = None, **fields: Any) -> dict[str, Any]:
    body = {"email": email or new_email(), "password": PASSWORD, "full_name": "Asha Menon"}
    response = client.post("/api/v1/auth/register", json=body | fields)
    assert response.status_code == 201, response.text
    return response.json()


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    """The JSON body plus the refresh token read from the cookie, under "refresh_token"."""
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json() | {"refresh_token": refresh_cookie(client, response)}


def refresh_cookie(client: TestClient, response: Any) -> str:
    token = response.cookies[REFRESH_COOKIE]
    client.cookies.clear()  # the next request carries only the cookie a test chooses
    return token


def send_refresh(client: TestClient, token: str) -> Any:
    """POST /auth/refresh as a browser holding `token` in its cookie."""
    return client.post("/api/v1/auth/refresh", headers=cookie_header(token))


def refreshed(client: TestClient, token: str) -> dict[str, Any]:
    response = send_refresh(client, token)
    assert response.status_code == 200, response.text
    return response.json() | {"refresh_token": refresh_cookie(client, response)}


def cookie_header(token: str) -> dict[str, str]:
    return {"Cookie": f"{REFRESH_COOKIE}={token}"}


def registered_and_logged_in(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """A new user and their tokens: the access token and the cookie's refresh token."""
    user = register(client)
    return user, login(client, user["email"])


def bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}
