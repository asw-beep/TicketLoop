"""Redis for the M10 tests: its own database, empty at the start of each test.

The rest of the suite runs with caching and rate limiting off (base_settings), so no test can
see another's cached pages or spent tokens. These tests turn them on, against database 15.
"""

import os
import socket
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Connection
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.storage import InMemoryStorage
from app.main import create_app
from app.modules.identity.models import UserRole
from scripts.make_admin import set_role
from tests.integration.api import login, register

REDIS_TEST_URL = "redis://localhost:6379/15"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def nowhere() -> str:
    """A Redis URL where nothing listens."""
    return f"redis://127.0.0.1:{free_port()}/0"


@pytest.fixture
def redis_url() -> str:
    """Skipped without Redis, like Postgres; CI sets REQUIRE_REDIS=1 to fail instead."""
    client = Redis.from_url(REDIS_TEST_URL, socket_timeout=1, socket_connect_timeout=1)
    try:
        client.flushdb()
    except RedisError:
        if os.environ.get("REQUIRE_REDIS") == "1":
            pytest.fail("Redis is required (REQUIRE_REDIS=1) but is not reachable.")
        pytest.skip("Redis is not running. Start it with `docker compose up -d redis`.")
    finally:
        client.close()
    return REDIS_TEST_URL


def app_with(test_settings: Settings, db_connection: Connection, **settings: Any) -> FastAPI:
    """The real app on the rolled-back test database, with these settings changed."""
    app = create_app(test_settings.model_copy(update=settings))
    factory = create_session_factory(db_connection)
    factory.configure(join_transaction_mode="create_savepoint")
    app.state.session_factory = factory
    app.state.storage = InMemoryStorage()
    return app


def client_for(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def account(
    client: TestClient, db: Session | None = None, *, admin: bool = False
) -> dict[str, str]:
    user = register(client)
    token = login(client, user["email"])["access_token"]
    if admin:
        assert db is not None
        set_role(db, user["email"], UserRole.ADMIN)
        db.flush()
    return {"id": user["id"], "email": user["email"], "token": token}
