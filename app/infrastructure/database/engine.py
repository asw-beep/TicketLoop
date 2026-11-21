"""The SQLAlchemy engine: one connection pool per process.

Creating an engine does not connect to anything. The first connection opens when something
asks for one, so the app can start while Postgres is still down; /health then reports it.
M2 adds a session factory next to this.
"""

from sqlalchemy import Engine, create_engine

from app.core.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        # Test each pooled connection with a cheap round trip before handing it out, so a
        # connection that died while idle (Postgres restarted) is replaced instead of failing.
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        connect_args={"connect_timeout": settings.db_connect_timeout_seconds},
    )
