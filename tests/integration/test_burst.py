"""A burst of signed-in requests, many more than the pool has connections: found by the M12
spike, where 1,000 buyers at once got 131 500s and 688 timeouts from four API containers.

FastAPI runs each sync dependency in a thread of its own, then the route in another. If the
sign-in lookup keeps its connection checked out until the route runs, a burst deadlocks: every
thread waits for a connection, and every connection belongs to a request waiting for a thread,
until the pool's timeout turns them into 500s.
"""

import asyncio
from collections.abc import Iterator

import anyio
import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.core.config import Settings
from app.infrastructure.database.session import create_session_factory
from app.main import create_app
from tests.integration.api import bearer
from tests.integration.conftest import new_account

pytestmark = pytest.mark.integration

REQUESTS = 20
THREADS = 4  # what anyio's 40 are to the spike's 250 requests per container
CONNECTIONS = 2  # what the pool's 15 are


@pytest.fixture
def small_app(test_settings: Settings) -> Iterator[FastAPI]:
    """The real app on a pool of two connections that gives up after 3 seconds."""
    app = create_app(test_settings)
    engine = create_engine(
        test_settings.database_url, pool_size=CONNECTIONS, max_overflow=0, pool_timeout=3
    )
    app.state.engine.dispose()
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    yield app
    engine.dispose()


def test_a_burst_of_signed_in_requests_does_not_starve_the_pool(small_app: FastAPI) -> None:
    with TestClient(small_app) as client:
        token = new_account(client)["token"]  # committed for real: this app has no test transaction

    async def burst() -> list[int]:
        anyio.to_thread.current_default_thread_limiter().total_tokens = THREADS
        transport = httpx.ASGITransport(app=small_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
            replies = await asyncio.gather(
                *[api.get("/api/v1/orders/me", headers=bearer(token)) for _ in range(REQUESTS)]
            )
        return [reply.status_code for reply in replies]

    assert anyio.run(burst) == [200] * REQUESTS
