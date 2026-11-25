"""Public search: what is shown, what each filter and sort does, and what is never shown."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.events.models import Event
from app.modules.listings.models import Listing, ListingStatus
from tests.integration.api import bearer, problem_code
from tests.integration.factories import make_event, make_listing, make_ticket

pytestmark = pytest.mark.integration

LISTINGS = "/api/v1/listings"


def on_sale(
    db: Session, event: Event | None = None, *, category: str = "GENERAL", **fields: Any
) -> Listing:
    event = event or make_event(db, status="PUBLISHED")
    ticket = make_ticket(
        db, event_id=event.id, original_price_paise=1_000_000, ticket_category=category
    )
    defaults = {"status": "ACTIVE", "asking_price_paise": 200_000}
    return make_listing(db, ticket=ticket, **(defaults | fields))


def search(api: TestClient, path: str = LISTINGS, **params: Any) -> list[dict[str, Any]]:
    response = api.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def ids(items: list[dict[str, Any]]) -> list[str]:
    return [item["id"] for item in items]


def test_search_needs_no_login_and_shows_only_the_public_shape(
    api: TestClient, db_session: Session
) -> None:
    event = make_event(
        db_session, status="PUBLISHED", name="Monsoon Beats", venue="Arena", city="Kochi"
    )
    listing = on_sale(db_session, event)

    [item] = search(api)

    assert item == {
        "id": str(listing.id),
        "event": {
            "id": str(event.id),
            "name": "Monsoon Beats",
            "category": "CONCERT",
            "city": "Kochi",
            "venue": "Arena",
            "starts_at": item["event"]["starts_at"],
        },
        "ticket_category": "GENERAL",
        "seat_details": None,
        "asking_price_paise": 200_000,
        "original_price_paise": 1_000_000,
        "status": "ACTIVE",
        "created_at": item["created_at"],
    }


@pytest.mark.parametrize("status", [s for s in ListingStatus if s != "ACTIVE"])
def test_only_active_listings_are_found(
    api: TestClient, db_session: Session, status: ListingStatus
) -> None:
    on_sale(db_session, status=status)

    assert search(api) == []


def test_the_seller_does_not_see_their_own_draft_in_search(
    api: TestClient, db_session: Session, user: dict[str, str]
) -> None:
    on_sale(db_session, status="DRAFT", seller_id=uuid.UUID(user["id"]))

    response = api.get(LISTINGS, headers=bearer(user["token"]))

    assert response.json()["items"] == []


def test_an_active_listing_past_its_end_is_not_found(api: TestClient, db_session: Session) -> None:
    # The expiry job has not reached it yet; it can no longer be reserved.
    on_sale(db_session, expires_at=datetime.now(UTC) - timedelta(seconds=1))

    assert search(api) == []


@pytest.mark.parametrize("q", ["monsoon", "BEATS", "soon be", "  monsoon  "])
def test_the_name_search_matches_any_part_in_any_case(
    api: TestClient, db_session: Session, q: str
) -> None:
    wanted = on_sale(db_session, make_event(db_session, status="PUBLISHED", name="Monsoon Beats"))
    on_sale(db_session, make_event(db_session, status="PUBLISHED", name="Winter Jazz"))

    assert ids(search(api, q=q)) == [str(wanted.id)]


@pytest.mark.parametrize(
    ("q", "name"), [("100%", "100% Live"), ("a_b", "a_b night"), ("\\", "back\\slash")]
)
def test_wildcards_in_a_search_mean_themselves(
    api: TestClient, db_session: Session, q: str, name: str
) -> None:
    wanted = on_sale(db_session, make_event(db_session, status="PUBLISHED", name=name))
    on_sale(db_session, make_event(db_session, status="PUBLISHED", name="100 Live axb"))

    assert ids(search(api, q=q)) == [str(wanted.id)]


@pytest.mark.parametrize("field", ["q", "city", "ticket_category"])
def test_a_blank_text_filter_is_no_filter(api: TestClient, db_session: Session, field: str) -> None:
    on_sale(db_session)

    assert len(search(api, **{field: "   "})) == 1


@pytest.mark.parametrize("field", ["q", "city", "ticket_category"])
def test_a_nul_byte_in_a_text_filter_is_422_not_500(api: TestClient, field: str) -> None:
    assert api.get(f"{LISTINGS}?{field}=a%00b").status_code == 422


def test_filter_by_event_category_city_and_ticket_category(
    api: TestClient, db_session: Session
) -> None:
    wanted = on_sale(
        db_session,
        make_event(db_session, status="PUBLISHED", category="SPORTS", city="Kochi"),
        category="VIP",
    )
    on_sale(db_session, make_event(db_session, status="PUBLISHED", category="CONCERT", city="Pune"))

    assert ids(search(api, category="SPORTS")) == [str(wanted.id)]
    assert ids(search(api, city="kochi")) == [str(wanted.id)]
    assert ids(search(api, ticket_category="vip")) == [str(wanted.id)]
    assert ids(search(api, category="SPORTS", city="Kochi", ticket_category="VIP")) == [
        str(wanted.id)
    ]
    assert search(api, category="SPORTS", city="Pune") == []


def test_filter_by_price_is_inclusive_at_both_ends(api: TestClient, db_session: Session) -> None:
    listings = [on_sale(db_session, asking_price_paise=p) for p in (100_000, 200_000, 300_000)]

    found = search(api, min_price_paise=100_000, max_price_paise=200_000)

    assert ids(found) == [str(listings[0].id), str(listings[1].id)]


def test_a_price_range_upside_down_is_422(api: TestClient) -> None:
    response = api.get(LISTINGS, params={"min_price_paise": 200, "max_price_paise": 100})

    assert response.status_code == 422
    assert problem_code(response) == "bad-price-range"


@pytest.mark.parametrize(
    "params",
    [
        {"min_price_paise": -1},
        {"max_price_paise": 10_000_001},
        {"min_price_paise": "cheap"},
        {"category": "OPERA"},
        {"event_id": "nope"},
        {"sort": "seller"},
        {"q": "x" * 101},
        {"limit": 101},
    ],
)
def test_a_bad_filter_is_422(api: TestClient, params: dict[str, Any]) -> None:
    assert api.get(LISTINGS, params=params).status_code == 422


def test_filter_by_event_id(api: TestClient, db_session: Session) -> None:
    wanted = on_sale(db_session)
    on_sale(db_session)

    assert ids(search(api, event_id=str(wanted.event_id))) == [str(wanted.id)]


@pytest.mark.parametrize(
    ("sort", "order"),
    [
        (None, [1, 0, 2]),
        ("price", [1, 0, 2]),
        ("-price", [2, 0, 1]),
        ("created_at", [0, 1, 2]),
        ("-created_at", [2, 1, 0]),
    ],
)
def test_sorting(api: TestClient, db_session: Session, sort: str | None, order: list[int]) -> None:
    base = datetime.now(UTC) - timedelta(days=1)
    made = [
        on_sale(db_session, asking_price_paise=price, created_at=base + timedelta(hours=i))
        for i, price in enumerate([200_000, 100_000, 300_000])
    ]

    found = search(api, **({"sort": sort} if sort else {}))

    assert ids(found) == [str(made[i].id) for i in order]


@pytest.mark.parametrize("sort", ["price", "-price", "created_at", "-created_at"])
def test_paging_visits_every_listing_once(api: TestClient, db_session: Session, sort: str) -> None:
    event = make_event(db_session, status="PUBLISHED")
    made = {
        str(on_sale(db_session, event, asking_price_paise=p).id)
        for p in (100_000, 100_000, 200_000, 100_000, 200_000)
    }
    seen, cursor = [], None
    while True:
        params = {"sort": sort, "limit": 2} | ({"cursor": cursor} if cursor else {})
        page = api.get(LISTINGS, params=params).json()
        seen += ids(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == 5
    assert set(seen) == made


def test_a_cursor_from_another_sort_is_400(api: TestClient, db_session: Session) -> None:
    event = make_event(db_session, status="PUBLISHED")
    for _ in range(3):
        on_sale(db_session, event)
    cursor = api.get(LISTINGS, params={"sort": "price", "limit": 2}).json()["next_cursor"]

    response = api.get(LISTINGS, params={"sort": "-price", "limit": 2, "cursor": cursor})

    assert response.status_code == 400
    assert problem_code(response) == "bad-cursor"


def test_the_listings_of_one_event(api: TestClient, db_session: Session) -> None:
    event = make_event(db_session, status="PUBLISHED")
    cheap = on_sale(db_session, event, asking_price_paise=100_000)
    dear = on_sale(db_session, event, asking_price_paise=300_000)
    on_sale(db_session)
    path = f"/api/v1/events/{event.id}/listings"

    assert ids(search(api, path)) == [str(cheap.id), str(dear.id)]
    assert ids(search(api, path, sort="-price")) == [str(dear.id), str(cheap.id)]
    assert ids(search(api, path, min_price_paise=200_000)) == [str(dear.id)]


def test_another_events_id_in_the_query_does_not_widen_one_events_listings(
    api: TestClient, db_session: Session
) -> None:
    event = make_event(db_session, status="PUBLISHED")
    other = on_sale(db_session)

    assert search(api, f"/api/v1/events/{event.id}/listings", event_id=str(other.event_id)) == []


@pytest.mark.parametrize("status", ["DRAFT", "CANCELLED", "COMPLETED"])
def test_the_listings_of_an_unpublished_event_are_404(
    api: TestClient, db_session: Session, status: str
) -> None:
    event = make_event(db_session, status=status)

    response = api.get(f"/api/v1/events/{event.id}/listings")

    assert response.status_code == 404
    assert problem_code(response) == "event-not-found"


def test_the_event_list_filters(api: TestClient, db_session: Session) -> None:
    soon = datetime.now(UTC) + timedelta(days=5)
    wanted = make_event(
        db_session,
        status="PUBLISHED",
        name="Monsoon Beats",
        city="Kochi",
        category="CONCERT",
        starts_at=soon,
    )
    make_event(
        db_session,
        status="PUBLISHED",
        name="City Derby",
        city="Pune",
        category="SPORTS",
        starts_at=soon + timedelta(days=20),
    )
    make_event(db_session, status="DRAFT", name="Monsoon Draft", city="Kochi", starts_at=soon)
    path = "/api/v1/events"

    for params in (
        {"q": "monsoon"},
        {"city": "KOCHI"},
        {"category": "CONCERT"},
        {"starts_before": (soon + timedelta(days=1)).isoformat()},
        {"q": "beats", "city": "Kochi", "category": "CONCERT"},
    ):
        assert ids(search(api, path, **params)) == [str(wanted.id)], params
    after = search(api, path, starts_after=(soon + timedelta(days=1)).isoformat())
    assert [item["name"] for item in after] == ["City Derby"]
    assert len(search(api, path)) == 2


@pytest.mark.parametrize(
    "params",
    [{"starts_after": "2027-01-15T18:30:00"}, {"starts_before": "soon"}, {"category": "OPERA"}],
)
def test_a_bad_event_filter_is_422(api: TestClient, params: dict[str, str]) -> None:
    assert api.get("/api/v1/events", params=params).status_code == 422


def test_a_nul_byte_in_an_event_search_is_422(api: TestClient) -> None:
    assert api.get("/api/v1/events?q=a%00b").status_code == 422
