"""Health against a real Postgres. Skipped when Postgres is not running (see conftest.py)."""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_health_reports_a_live_database(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}
