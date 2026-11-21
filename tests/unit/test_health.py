from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.health import database_is_reachable
from app.core.config import Settings
from app.main import create_app


def test_health_is_200_when_database_is_reachable(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[database_is_reachable] = lambda: True

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}


def test_health_is_503_when_database_is_unreachable(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[database_is_reachable] = lambda: False

    response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "checks": {"database": "unavailable"}}


def test_health_really_detects_a_dead_database() -> None:
    # No override: the real check runs against a port where nothing listens.
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+psycopg://nobody:nothing@127.0.0.1:1/none",
        db_connect_timeout_seconds=2,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unavailable"
