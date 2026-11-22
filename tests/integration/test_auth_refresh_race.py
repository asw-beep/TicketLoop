"""Two requests refresh the same token at the same moment.

Unlike the other integration tests, these requests really commit, from separate connections,
so Postgres row locks decide the outcome exactly as in production. The rows are deleted
afterwards.

Expected, whatever the timing: one request gets a new pair, the other gets 401, and because
a spent token came back, the whole family ends up revoked, the winner's new token included.
"""

import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import create_app
from app.modules.identity.models import RefreshToken, User
from tests.integration.api import cookie_header, registered_and_logged_in

pytestmark = pytest.mark.integration

ROUNDS = 20


@pytest.fixture
def committing_app(test_settings: Settings, test_engine: Engine) -> Iterator[FastAPI]:
    app = create_app(test_settings)  # its own engine and pool: every request commits for real
    yield app
    with Session(test_engine) as session, session.begin():
        users = select(User.id).where(User.full_name == "Asha Menon")
        session.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(users)))
        session.execute(delete(User).where(User.id.in_(users)))


@dataclass(frozen=True)
class Attempt:
    status: int
    started: float
    finished: float


def refresh_twice_at_once(
    api: TestClient, pool: ThreadPoolExecutor, token: str
) -> tuple[Attempt, Attempt]:
    start = threading.Barrier(2)

    def attempt(_: int) -> Attempt:
        start.wait()  # release both requests together
        started = time.perf_counter()
        # An explicit Cookie header: both requests carry the same token, whatever the
        # client's cookie jar learns from the other response meanwhile.
        response = api.post("/api/v1/auth/refresh", headers=cookie_header(token))
        return Attempt(response.status_code, started, time.perf_counter())

    first, second = pool.map(attempt, range(2))
    return first, second


def test_two_refreshes_of_one_token_at_once_have_one_winner_and_revoke_the_family(
    committing_app: FastAPI, test_engine: Engine
) -> None:
    overlapping_rounds = 0
    with TestClient(committing_app) as api, ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(ROUNDS):
            user, pair = registered_and_logged_in(api)

            a, b = refresh_twice_at_once(api, pool, pair["refresh_token"])

            assert sorted([a.status, b.status]) == [200, 401]
            if a.started < b.finished and b.started < a.finished:
                overlapping_rounds += 1
            with Session(test_engine) as session:
                live = session.scalars(
                    select(RefreshToken.id).where(
                        RefreshToken.user_id == user["id"], RefreshToken.revoked_at.is_(None)
                    )
                ).all()
            assert live == []

    # Guard against a test that passes only because the requests ran one after the other.
    assert overlapping_rounds > 0
