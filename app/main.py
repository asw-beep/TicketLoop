"""The app factory. `uvicorn app.main:app` imports the module-level `app` below.

A factory (a function that builds the app) rather than a global built line by line, so
tests can build a fresh app with their own Settings and nothing leaks between tests.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.router import build_api_router
from app.core.body_limit import BodySizeLimitMiddleware
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.infrastructure.database.engine import create_db_engine
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.external_providers.email import (
    EmailSender,
    InMemoryEmailSender,
    SmtpEmailSender,
)
from app.infrastructure.storage import S3Storage, StorageError

log = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Code before `yield` runs once at startup, code after it once at shutdown.
        log.info("app.startup", environment=settings.environment)
        if settings.environment == "local":
            # On a laptop, create the bucket on first run. Deployed environments provision it
            # outside the app; tests create their own. Without MinIO the API still starts,
            # and uploads answer 503 until it is back.
            try:
                app.state.storage.ensure_bucket()
            except StorageError as error:
                log.warning("storage.unavailable", error=str(error))
        yield
        app.state.engine.dispose()  # close pooled connections cleanly
        log.info("app.shutdown")

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    # app.state is where shared, process-wide objects live; dependencies read them from there.
    app.state.settings = settings
    app.state.engine = create_db_engine(settings)
    app.state.session_factory = create_session_factory(app.state.engine)  # used by get_db
    app.state.email_sender = create_email_sender(settings)
    app.state.storage = S3Storage.from_settings(settings)  # builds clients; no network call

    # Middleware added last runs first. The size limit sits inside the request context, so
    # even a refused upload gets a request ID and an access-log line.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(build_api_router(settings.api_prefix))
    return app


def create_email_sender(settings: Settings) -> EmailSender:
    if settings.smtp_server is None:
        return InMemoryEmailSender()
    host, port = settings.smtp_server
    return SmtpEmailSender(host=host, port=port, sender=settings.email_from)


app = create_app()
