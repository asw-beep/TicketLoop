"""Fixtures for races. Everything here commits for real, in a database of its own that is
recreated once per run."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, make_url

from app.core.config import Settings
from app.infrastructure.database.engine import create_db_engine
from scripts.databases import database_url_with_name, recreate_database, upgrade
from tests.concurrency.harness import (
    ApiServer,
    Buyer,
    FakepayServer,
    free_port,
    make_buyers,
)
from tests.integration.conftest import base_settings, require_postgres  # noqa: F401


@pytest.fixture(scope="session")
def race_settings(base_settings: Settings) -> Settings:  # noqa: F811
    name = f"{make_url(base_settings.database_url).database}_race_test"
    url = database_url_with_name(base_settings.database_url, name)
    return base_settings.model_copy(update={"database_url": url})


@pytest.fixture(scope="session")
def race_engine(race_settings: Settings, require_postgres: None) -> Iterator[Engine]:  # noqa: F811
    recreate_database(race_settings.database_url)
    engine = create_db_engine(race_settings)
    upgrade(engine, "head")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def fakepay_url() -> str:
    return f"http://127.0.0.1:{free_port()}"


@pytest.fixture(scope="session")
def api_server(
    race_settings: Settings, race_engine: Engine, fakepay_url: str
) -> Iterator[ApiServer]:
    """The API on four worker processes."""
    with ApiServer(race_settings, env={"FAKEPAY_URL": fakepay_url}) as server:
        yield server


@pytest.fixture(scope="session")
def fakepay_server(api_server: ApiServer, fakepay_url: str) -> Iterator[FakepayServer]:
    """The gateway, for the races that really start a payment."""
    webhook_url = f"{api_server.urls[0]}/api/v1/payments/webhooks/fakepay"
    with FakepayServer(fakepay_url, webhook_url) as server:
        yield server


@pytest.fixture(scope="session")
def buyers(race_engine: Engine, race_settings: Settings) -> list[Buyer]:
    return make_buyers(race_engine, race_settings)
