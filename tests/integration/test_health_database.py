"""Health against a real Postgres. Skipped when Postgres is not running."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.core.config import Settings

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def require_postgres(settings: Settings) -> None:
    engine = create_engine(settings.database_url, connect_args={"connect_timeout": 2})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("Postgres is not running. Start it with `docker compose up -d db`.")
    finally:
        engine.dispose()


def test_health_reports_a_live_database(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}
