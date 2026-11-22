"""Fixtures for tests that need a real Postgres.

Once per test run: drop and recreate `<db>_test`, then build it with `alembic upgrade head`,
so every run also proves the migrations work on an empty database.

Per test: one connection with an open transaction that is rolled back afterwards. The app's
sessions join it through SAVEPOINTs, so code under test can commit freely and the database is
still clean for the next test.
"""

import os
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.infrastructure.database.engine import create_db_engine
from app.infrastructure.database.session import create_session_factory
from app.main import create_app
from tests.integration.database import database_url_with_name, recreate_database, upgrade


@pytest.fixture(scope="session")
def base_settings() -> Settings:
    return Settings(_env_file=None, environment="test")


@pytest.fixture(scope="session", autouse=True)
def require_postgres(base_settings: Settings) -> None:
    engine = create_engine(base_settings.database_url, connect_args={"connect_timeout": 2})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError:
        # CI sets REQUIRE_POSTGRES=1: there, a missing database is a broken pipeline, and a
        # skip would let it pass green with half the tests never run.
        if os.environ.get("REQUIRE_POSTGRES") == "1":
            pytest.fail("Postgres is required (REQUIRE_POSTGRES=1) but is not reachable.")
        pytest.skip("Postgres is not running. Start it with `docker compose up -d db`.")
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def test_settings(base_settings: Settings) -> Settings:
    name = f"{make_url(base_settings.database_url).database}_test"
    url = database_url_with_name(base_settings.database_url, name)
    return base_settings.model_copy(update={"database_url": url})


@pytest.fixture(scope="session")
def test_engine(test_settings: Settings) -> Iterator[Engine]:
    recreate_database(test_settings.database_url)
    engine = create_db_engine(test_settings)
    upgrade(engine, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db_connection(test_engine: Engine) -> Iterator[Connection]:
    connection = test_engine.connect()
    transaction = connection.begin()
    yield connection
    transaction.rollback()  # undo everything the test did, commits included
    connection.close()


@pytest.fixture
def db_session(db_connection: Connection) -> Iterator[Session]:
    factory = create_session_factory(db_connection)
    # Each session commit becomes RELEASE SAVEPOINT; the outer transaction stays open.
    factory.configure(join_transaction_mode="create_savepoint")
    with factory() as session:
        yield session


@pytest.fixture
def db_app(test_settings: Settings, db_connection: Connection) -> FastAPI:
    """The real app, with its sessions joined to this test's rolled-back transaction."""
    app = create_app(test_settings)
    factory = create_session_factory(db_connection)
    factory.configure(join_transaction_mode="create_savepoint")
    app.state.session_factory = factory
    return app


@pytest.fixture
def api(db_app: FastAPI) -> Iterator[TestClient]:
    """A client for the real app on the rolled-back test database."""
    with TestClient(db_app) as client:
        yield client
