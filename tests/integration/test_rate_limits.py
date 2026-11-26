"""Rate limits: a token bucket per endpoint, a polite 429, and nothing refused when Redis is
down or limiting is off."""

import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.infrastructure.redis import Breaker, TokenBucket, create_redis
from tests.integration.api import PASSWORD, bearer, problem_code, register
from tests.integration.factories import make_event, make_listing, make_verified_ticket
from tests.integration.redis_support import (
    account,
    app_with,
    client_for,
    nowhere,
    redis_url,  # noqa: F401
)

pytestmark = pytest.mark.integration

LOGIN = "/api/v1/auth/login"


@pytest.fixture
def limited(test_settings: Settings, db_connection: Connection, redis_url: str):  # noqa: F811
    """A client for the app with rate limits on, and these settings changed."""

    def make(**settings: Any) -> Iterator[TestClient]:
        app = app_with(
            test_settings,
            db_connection,
            **({"redis_url": redis_url, "rate_limit_enabled": True} | settings),
        )
        return client_for(app)

    return make


@pytest.fixture
def api(limited: Any) -> Iterator[TestClient]:
    yield from limited()


def log_in(api: TestClient, email: str, password: str = "not the password") -> Any:
    return api.post(LOGIN, json={"email": email, "password": password})


def assert_limited(response: Any) -> int:
    assert response.status_code == 429, response.text
    assert problem_code(response) == "rate-limited"
    wait = int(response.headers["Retry-After"])
    assert 1 <= wait <= 60
    return wait


def test_the_sixth_login_in_a_minute_is_refused_even_with_the_right_password(
    api: TestClient,
) -> None:
    email = register(api)["email"]
    assert [log_in(api, email).status_code for _ in range(5)] == [401] * 5

    wait = assert_limited(log_in(api, email, PASSWORD))

    assert wait >= 10  # 5 a minute: the next token is about 12 seconds away


def test_the_same_email_in_other_casing_shares_the_bucket(api: TestClient) -> None:
    email = register(api)["email"]
    for _ in range(5):
        log_in(api, email.upper())
    assert_limited(log_in(api, email))


def test_another_email_has_a_bucket_of_its_own(api: TestClient) -> None:
    first, second = register(api)["email"], register(api)["email"]
    for _ in range(5):
        log_in(api, first)
    assert_limited(log_in(api, first))
    assert log_in(api, second).status_code == 401


def test_the_429_says_nothing_about_whether_the_account_exists(api: TestClient) -> None:
    real, made_up = register(api)["email"], f"nobody-{uuid.uuid4().hex[:8]}@example.com"
    for _ in range(5):
        log_in(api, real)
        log_in(api, made_up)

    one, other = log_in(api, real), log_in(api, made_up)

    assert_limited(one)
    assert_limited(other)
    assert one.json() | {"request_id": None} == other.json() | {"request_id": None}


def test_password_reset_requests_are_limited_the_same_way(api: TestClient) -> None:
    email = register(api)["email"]
    path = "/api/v1/auth/password-reset/request"
    answers = [api.post(path, json={"email": email}).status_code for _ in range(5)]
    assert answers == [202] * 5
    assert_limited(api.post(path, json={"email": email}))


def test_reserving_is_limited_per_user(limited: Any, db_session: Session) -> None:
    for api in limited(rate_limit_reserve_per_minute=3):
        event = make_event(db_session, status="PUBLISHED")
        ticket = make_verified_ticket(db_session, event_id=event.id)
        listing = make_listing(db_session, ticket=ticket, status="ACTIVE")
        buyer, other = account(api), account(api)
        path = f"/api/v1/listings/{listing.id}/reservations"
        # Asking again for the hold you have counts too: it is still a request.
        assert [api.post(path, headers=bearer(buyer["token"])).status_code for _ in range(3)] == [
            201,
            200,
            200,
        ]

        assert_limited(api.post(path, headers=bearer(buyer["token"])))
        assert api.post(path, headers=bearer(other["token"])).status_code == 409


def start_paying(api: TestClient, token: str) -> Any:
    return api.post(
        "/api/v1/payments/intents",
        json={"order_id": str(uuid.uuid4())},
        headers=bearer(token) | {"Idempotency-Key": uuid.uuid4().hex},
    )


def test_payment_intents_are_limited_per_user(limited: Any) -> None:
    for api in limited(rate_limit_payment_per_minute=2):
        token = account(api)["token"]
        assert [start_paying(api, token).status_code for _ in range(2)] == [404, 404]
        assert_limited(start_paying(api, token))


@pytest.mark.parametrize("path", ["/api/v1/listings", "/api/v1/events/{event_id}/listings"])
def test_searching_is_limited_per_address(limited: Any, db_session: Session, path: str) -> None:
    event = make_event(db_session, status="PUBLISHED")
    url = path.format(event_id=event.id)
    for api in limited(rate_limit_search_per_minute=3):
        assert [api.get(url).status_code for _ in range(3)] == [200] * 3
        assert_limited(api.get(url))


def test_the_bucket_fills_up_again(redis_url: str) -> None:  # noqa: F811
    bucket = TokenBucket(create_redis(Settings(_env_file=None, redis_url=redis_url)), Breaker())
    # 600 a minute is one every 0.1 seconds, so it refills while being drained: take until
    # the first refusal, which comes after at least the 600 a full bucket holds.
    taken, decision = 0, bucket.take("test", "me", 600)
    while decision.allowed:
        taken += 1
        assert taken < 5_000
        decision = bucket.take("test", "me", 600)
    assert taken >= 600
    assert 0 < decision.retry_after <= 0.1

    time.sleep(decision.retry_after + 0.05)

    assert bucket.take("test", "me", 600).allowed


def test_with_redis_down_nothing_is_refused(
    test_settings: Settings, db_connection: Connection
) -> None:
    down = app_with(test_settings, db_connection, redis_url=nowhere(), rate_limit_enabled=True)
    for api in client_for(down):
        email = register(api)["email"]
        assert {log_in(api, email).status_code for _ in range(8)} == {401}


def test_with_limiting_off_nothing_is_refused(limited: Any) -> None:
    for api in limited(rate_limit_enabled=False):
        email = register(api)["email"]
        assert {log_in(api, email).status_code for _ in range(8)} == {401}
