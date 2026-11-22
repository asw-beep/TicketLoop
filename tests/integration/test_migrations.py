"""The migrations build the schema, tear it down, and match the models exactly."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect, select, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.config import Settings
from app.infrastructure.database.base import Base
from app.modules.events.models import TicketProvider
from tests.integration.database import (
    database_url_with_name,
    downgrade,
    recreate_database,
    upgrade,
)

pytestmark = pytest.mark.integration

M2_TABLES = {"users", "ticket_providers", "events", "tickets", "listings", "audit_logs"}
M3_TABLES = {"refresh_tokens", "password_reset_tokens"}
M4_TABLES = {"ticket_documents"}
ALL_TABLES = M2_TABLES | M3_TABLES | M4_TABLES


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
        assert tables(engine) >= ALL_TABLES

        downgrade(engine, "base")
        assert tables(engine) <= {"alembic_version"}
        assert not function_exists(engine, "forbid_update_delete")

        upgrade(engine, "head")
        assert tables(engine) >= ALL_TABLES
    finally:
        engine.dispose()


def test_m4_goes_down_and_up_again_while_an_event_uses_the_seeded_provider(
    test_settings: Settings,
) -> None:
    # The downgrade keeps fakeprovider when events use it, so the upgrade must cope with it
    # already being there: down-and-up is the everyday loop while writing the next migration.
    url = database_url_with_name(test_settings.database_url, "ticketloop_migrations_test")
    recreate_database(url)
    engine = create_engine(url)
    try:
        upgrade(engine, "head")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO events (provider_id, name, category, venue, city, starts_at, "
                    "organizer) SELECT id, 'Kept', 'CONCERT', 'Hall', 'Kochi', "
                    "now() + interval '30 days', 'Org' FROM ticket_providers "
                    "WHERE code = 'fakeprovider'"
                )
            )

        downgrade(engine, "-1")
        upgrade(engine, "head")

        with engine.connect() as connection:
            providers = connection.scalar(
                text("SELECT count(*) FROM ticket_providers WHERE code = 'fakeprovider'")
            )
        assert providers == 1
    finally:
        engine.dispose()


def test_the_models_and_the_migrations_describe_the_same_schema(test_engine: Engine) -> None:
    # The same check as `alembic check`: autogenerate finds nothing left to migrate.
    with test_engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert differences == []


def test_the_fake_provider_is_seeded(db_session: Session) -> None:
    name = db_session.scalar(
        select(TicketProvider.name).where(TicketProvider.code == "fakeprovider")
    )
    assert name == "FakeProvider"
