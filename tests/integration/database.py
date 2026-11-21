"""Test-only helpers: create throwaway databases and run Alembic against them."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, make_url, text

REPO_ROOT = Path(__file__).resolve().parents[2]


def database_url_with_name(url: str, name: str) -> str:
    """The same server and credentials, a different database."""
    return make_url(url).set(database=name).render_as_string(hide_password=False)


def recreate_database(url: str) -> None:
    """Drop the database in `url` if it exists, then create it empty."""
    target = make_url(url)
    # CREATE and DROP DATABASE cannot run inside a transaction, hence AUTOCOMMIT, and they
    # must run from a different database, hence the built-in `postgres` maintenance database.
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            name = connection.dialect.identifier_preparer.quote(target.database)
            # WITH (FORCE) closes leftover connections from an earlier, interrupted run.
            connection.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
            connection.execute(text(f"CREATE DATABASE {name}"))
    finally:
        admin.dispose()


def alembic_config(connection: Connection) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.attributes["connection"] = connection  # env.py runs on this connection
    return config


def upgrade(engine: Engine, revision: str = "head") -> None:
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), revision)


def downgrade(engine: Engine, revision: str = "base") -> None:
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), revision)
