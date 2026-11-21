"""Every error, whatever its cause, comes back as application/problem+json (RFC 9457)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.core.exceptions import ConflictError


class SignUp(BaseModel):
    email: str
    password: str = Field(min_length=12)


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    """The normal app plus a few routes that fail on purpose."""

    @app.get("/test/conflict")
    def conflict() -> None:
        raise ConflictError("Listing is RESERVED.", current_status="RESERVED")

    @app.post("/test/signup")
    def signup(body: SignUp) -> dict[str, str]:
        return {"email": body.email}

    @app.get("/test/crash")
    def crash() -> None:
        raise RuntimeError("database password is hunter2")

    return app


def test_unknown_path_is_a_problem_404(client: TestClient) -> None:
    response = client.get("/api/v1/nope")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Not Found"
    assert body["status"] == 404
    assert body["instance"] == "/api/v1/nope"
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_wrong_method_is_a_problem_405(client: TestClient) -> None:
    response = client.post("/api/v1/health")

    assert response.status_code == 405
    assert response.json()["title"] == "Method Not Allowed"


def test_app_error_keeps_its_type_detail_and_extensions(client: TestClient) -> None:
    response = client.get("/test/conflict")

    assert response.status_code == 409
    body = response.json()
    assert body["type"] == "urn:ticketloop:problem:conflict"
    assert body["detail"] == "Listing is RESERVED."
    assert body["current_status"] == "RESERVED"


def test_validation_error_lists_fields_without_echoing_input(client: TestClient) -> None:
    response = client.post("/test/signup", json={"email": "a@b.in", "password": "hunter2"})

    assert response.status_code == 422
    body = response.json()
    assert body["type"] == "urn:ticketloop:problem:validation-error"
    assert body["errors"][0]["location"] == ["body", "password"]
    assert "hunter2" not in response.text  # FastAPI's default 422 would echo the password


def test_unexpected_error_is_a_bare_500(app: FastAPI) -> None:
    # raise_server_exceptions=False: behave like a real server instead of re-raising in the test.
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test/crash")

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert "hunter2" not in response.text
    assert "RuntimeError" not in response.text
