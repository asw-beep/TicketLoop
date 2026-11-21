"""The app factory. `uvicorn app.main:app` imports the module-level `app` below.

A factory (a function that builds the app) rather than a global built line by line, so
tests can build a fresh app with their own Settings and nothing leaks between tests.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.router import build_api_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.infrastructure.database.engine import create_db_engine
from app.infrastructure.database.session import create_session_factory

log = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Code before `yield` runs once at startup, code after it once at shutdown.
        log.info("app.startup", environment=settings.environment)
        yield
        app.state.engine.dispose()  # close pooled connections cleanly
        log.info("app.shutdown")

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    # app.state is where shared, process-wide objects live; dependencies read them from there.
    app.state.settings = settings
    app.state.engine = create_db_engine(settings)
    app.state.session_factory = create_session_factory(app.state.engine)  # used by get_db

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(build_api_router(settings.api_prefix))
    return app


app = create_app()
