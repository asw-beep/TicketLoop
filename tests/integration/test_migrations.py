"""The migrations build the schema, tear it down, and match the models exactly."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect, text

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.config import Settings
from app.infrastructure.database.base import Base
from tests.integration.database import (
    database_url_with_name,
    downgrade,
    recreate_database,
    upgrade,
)

pytestmark = pytest.mark.integration

M2_TABLES = {"users", "ticket_providers", "events", "tickets", "listings", "audit_logs"}


def tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def function_exists(engine: Engine, name: str) -> bool:
    with engine.connect() as connection:
        found = connection.scalar(
            text("SELECT count(*) FROM pg_proc WHERE proname = :name"), {"name": name}
        )
    return found > 0


def test_migrations_go_down_to_base_and_back_up_to_head(test_settings: Settings) -> None:
    url = database_url_with_name(test_settings.database_url, "ticketloop_migrations_test")
    recreate_database(url)
    engine = create_engine(url)
    try:
        upgrade(engine, "head")
        assert tables(engine) >= M2_TABLES

        downgrade(engine, "base")
        assert tables(engine) <= {"alembic_version"}
        assert not function_exists(engine, "forbid_update_delete")

        upgrade(engine, "head")
        assert tables(engine) >= M2_TABLES
    finally:
        engine.dispose()


def test_the_models_and_the_migrations_describe_the_same_schema(test_engine: Engine) -> None:
    # The same check as `alembic check`: autogenerate finds nothing left to migrate.
    with test_engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert differences == []
