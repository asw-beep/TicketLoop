"""Cached reads: a second call is a hit, any change is seen at once, and a stale page is never
trusted with a decision."""

import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.events.models import Event
from app.modules.listings.models import Listing
from tests.integration.api import bearer, problem_code
from tests.integration.factories import make_event, make_listing, make_verified_ticket
from tests.integration.redis_support import (
    account,
    app_with,
    client_for,
    nowhere,
    redis_url,  # noqa: F401
)

pytestmark = pytest.mark.integration

LISTINGS = "/api/v1/listings"


@pytest.fixture
def api(
    test_settings: Settings,
    db_connection: Connection,
    redis_url: str,  # noqa: F811
) -> Iterator[TestClient]:
    app = app_with(test_settings, db_connection, redis_url=redis_url, cache_enabled=True)
    yield from client_for(app)


def on_sale(db: Session, event: Event | None = None, **fields: Any) -> Listing:
    event = event or make_event(db, status="PUBLISHED")
    ticket = make_verified_ticket(db, event_id=event.id, original_price_paise=1_000_000)
    return make_listing(db, ticket=ticket, **({"status": "ACTIVE"} | fields))


def get(api: TestClient, path: str, **params: Any) -> tuple[str, Any]:
    response = api.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.headers["X-Cache"], response.json()


def ids(body: dict[str, Any]) -> list[str]:
    return [item["id"] for item in body["items"]]


def test_the_second_search_is_a_hit_with_the_same_body(
    api: TestClient, db_session: Session
) -> None:
    on_sale(db_session)

    first = get(api, LISTINGS)
    second = get(api, LISTINGS)

    assert first[0] == "MISS"
    assert second == ("HIT", first[1])


@pytest.mark.parametrize(
    "params",
    [
        {"q": "monsoon"},
        {"category": "SPORTS"},
        {"city": "Kochi"},
        {"ticket_category": "VIP"},
        {"min_price_paise": 100},
        {"max_price_paise": 900_000},
        {"event_id": str(uuid.uuid4())},
        {"sort": "-price"},
        {"limit": 5},
    ],
)
def test_every_filter_the_sort_and_the_limit_are_part_of_the_key(
    api: TestClient, db_session: Session, params: dict[str, Any]
) -> None:
    on_sale(db_session)
    get(api, LISTINGS)
    assert get(api, LISTINGS)[0] == "HIT"

    assert get(api, LISTINGS, **params)[0] == "MISS"
    assert get(api, LISTINGS, **params)[0] == "HIT"


def test_a_later_page_is_not_cached(api: TestClient, db_session: Session) -> None:
    for _ in range(3):
        on_sale(db_session)
    first = get(api, LISTINGS, limit=2)[1]

    cache, _ = get(api, LISTINGS, limit=2, cursor=first["next_cursor"])

    assert cache == "BYPASS"


def test_a_reservation_shows_in_the_next_search(api: TestClient, db_session: Session) -> None:
    listing = on_sale(db_session)
    assert ids(get(api, LISTINGS)[1]) == [str(listing.id)]
    assert get(api, LISTINGS)[0] == "HIT"

    buyer = account(api)
    reserved = api.post(f"{LISTINGS}/{listing.id}/reservations", headers=bearer(buyer["token"]))
    assert reserved.status_code == 201, reserved.text

    cache, body = get(api, LISTINGS)
    assert (cache, ids(body)) == ("MISS", [])


def test_a_reservation_bumps_once_its_transaction_has_committed(
    api: TestClient,
    db_session: Session,
    db_connection: Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reserving releases a savepoint (the insert) before it commits. Bumped at that release,
    # a search in between would store the listing as on sale under the new version.
    listing = on_sale(db_session)
    db_session.commit()
    buyer = account(api)
    cache = api.app.state.cache  # type: ignore[attr-defined]
    during: list[bool] = []
    bump = cache.bump

    def watching(*names: str) -> None:
        during.append(db_connection.in_nested_transaction())
        bump(*names)

    monkeypatch.setattr(cache, "bump", watching)
    reserved = api.post(f"{LISTINGS}/{listing.id}/reservations", headers=bearer(buyer["token"]))

    assert reserved.status_code == 201, reserved.text
    assert during == [False]


def test_an_approval_and_a_cancellation_show_at_once(api: TestClient, db_session: Session) -> None:
    admin = account(api, db_session, admin=True)
    listing = on_sale(db_session, status="PENDING_VERIFICATION")
    assert ids(get(api, LISTINGS)[1]) == []

    approved = api.post(
        f"/api/v1/admin/listings/{listing.id}/approve", headers=bearer(admin["token"])
    )
    assert approved.status_code == 200, approved.text
    assert ids(get(api, LISTINGS)[1]) == [str(listing.id)]

    cancelled = api.delete(f"{LISTINGS}/{listing.id}", headers=bearer(admin["token"]))
    assert cancelled.status_code == 200, cancelled.text
    assert ids(get(api, LISTINGS)[1]) == []


def test_a_reservation_from_a_stale_search_still_gets_its_409(
    api: TestClient, db_session: Session
) -> None:
    listing = on_sale(db_session)
    assert ids(get(api, LISTINGS)[1]) == [str(listing.id)]
    # Sold behind the cache's back: the test session never commits, so nothing is bumped.
    db_session.execute(update(Listing).where(Listing.id == listing.id).values(status="SOLD"))
    db_session.flush()

    cache, body = get(api, LISTINGS)
    assert (cache, ids(body)) == ("HIT", [str(listing.id)])  # stale, on purpose

    buyer = account(api)
    response = api.post(f"{LISTINGS}/{listing.id}/reservations", headers=bearer(buyer["token"]))
    assert response.status_code == 409
    assert problem_code(response) == "listing-not-available"


def test_the_event_page_is_cached_and_follows_an_edit(api: TestClient, db_session: Session) -> None:
    admin = account(api, db_session, admin=True)
    event = make_event(db_session, status="PUBLISHED", name="Monsoon Beats")
    path = f"/api/v1/events/{event.id}"
    assert get(api, path)[0] == "MISS"
    assert get(api, path) == ("HIT", get(api, path)[1])

    edited = api.patch(
        f"/api/v1/admin/events/{event.id}",
        json={"name": "Monsoon Beats II"},
        headers=bearer(admin["token"]),
    )
    assert edited.status_code == 200, edited.text

    cache, body = get(api, path)
    assert (cache, body["name"]) == ("MISS", "Monsoon Beats II")


def test_an_events_listings_follow_the_event_being_cancelled(
    api: TestClient, db_session: Session
) -> None:
    admin = account(api, db_session, admin=True)
    event = make_event(db_session, status="PUBLISHED")
    listing = on_sale(db_session, event)
    path = f"/api/v1/events/{event.id}/listings"
    assert ids(get(api, path)[1]) == [str(listing.id)]
    assert get(api, path)[0] == "HIT"

    cancelled = api.patch(
        f"/api/v1/admin/events/{event.id}",
        json={"status": "CANCELLED"},
        headers=bearer(admin["token"]),
    )
    assert cancelled.status_code == 200, cancelled.text

    assert api.get(path).status_code == 404
    assert api.get(f"/api/v1/events/{event.id}").status_code == 404


def test_a_missing_event_is_not_cached(api: TestClient) -> None:
    path = f"/api/v1/events/{uuid.uuid4()}"
    assert api.get(path).status_code == 404
    assert api.get(path).status_code == 404


def test_with_redis_down_every_answer_is_the_same(
    test_settings: Settings, db_connection: Connection, api: TestClient, db_session: Session
) -> None:
    event = make_event(db_session, status="PUBLISHED")
    on_sale(db_session, event)
    on_sale(db_session, event)
    paths = [LISTINGS, f"/api/v1/events/{event.id}", f"/api/v1/events/{event.id}/listings"]
    cached = [get(api, path)[1] for path in paths]

    down = app_with(test_settings, db_connection, redis_url=nowhere(), cache_enabled=True)
    for client in client_for(down):
        started = time.monotonic()
        answers = [get(client, path) for path in paths for _ in range(3)]
        elapsed = time.monotonic() - started

    assert [body for _, body in answers] == [body for body in cached for _ in range(3)]
    assert {cache for cache, _ in answers} == {"BYPASS"}
    assert elapsed < 3  # one timeout at most, then the breaker skips Redis


def test_with_caching_off_every_answer_says_bypass(
    test_settings: Settings, db_connection: Connection, db_session: Session
) -> None:
    on_sale(db_session)
    off = app_with(test_settings, db_connection, cache_enabled=False)
    for client in client_for(off):
        assert get(client, LISTINGS)[0] == "BYPASS"
        assert get(client, LISTINGS)[0] == "BYPASS"
