"""Reading listings: who sees which listing, in which shape.

The seller and admins see any status in the full shape. Everyone else, signed in or not, sees
only ACTIVE listings, in the public shape; any other status is the same 404 as an unknown ID.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.events.models import Event
from app.modules.listings.models import ListingStatus
from app.modules.tickets.models import Ticket
from tests.integration.api import bearer
from tests.integration.conftest import new_account
from tests.integration.factories import make_listing, make_verified_ticket

pytestmark = pytest.mark.integration

LISTINGS = "/api/v1/listings"
UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"
PUBLIC_KEYS = {
    "id",
    "event",
    "ticket_category",
    "seat_details",
    "asking_price_paise",
    "original_price_paise",
    "status",
    "created_at",
}
EVENT_KEYS = {"id", "name", "category", "city", "venue", "starts_at"}


def without_request_details(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in {"instance", "request_id"}}


def test_anyone_sees_an_active_listing_in_its_public_shape(
    api: TestClient, db_session: Session, verified_ticket: Ticket
) -> None:
    listing = make_listing(db_session, ticket=verified_ticket, status="ACTIVE")
    stranger = new_account(api)

    for headers in ({}, bearer(stranger["token"])):
        response = api.get(f"{LISTINGS}/{listing.id}", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == PUBLIC_KEYS
        assert set(body["event"]) == EVENT_KEYS


@pytest.mark.parametrize("status", [s for s in ListingStatus if s != ListingStatus.ACTIVE])
def test_other_statuses_are_404_to_everyone_but_seller_and_admin(
    api: TestClient,
    db_session: Session,
    user: dict[str, str],
    admin: dict[str, str],
    verified_ticket: Ticket,
    status: ListingStatus,
) -> None:
    listing = make_listing(db_session, ticket=verified_ticket, status=status)
    stranger = new_account(api)
    unknown = api.get(f"{LISTINGS}/{UNKNOWN_ID}")
    assert unknown.status_code == 404

    for headers in ({}, bearer(stranger["token"])):
        response = api.get(f"{LISTINGS}/{listing.id}", headers=headers)
        assert response.status_code == 404
        assert without_request_details(response.json()) == without_request_details(unknown.json())
    for account in (user, admin):
        response = api.get(f"{LISTINGS}/{listing.id}", headers=bearer(account["token"]))
        assert response.status_code == 200
        assert response.json()["ticket_id"] == str(verified_ticket.id)


def test_a_bad_token_is_401_not_the_public_view(
    api: TestClient, db_session: Session, verified_ticket: Ticket
) -> None:
    listing = make_listing(db_session, ticket=verified_ticket, status="ACTIVE")

    response = api.get(f"{LISTINGS}/{listing.id}", headers=bearer("expired-or-forged"))

    assert response.status_code == 401


def test_my_listings_are_mine_newest_first_and_filter_by_status_and_ticket(
    api: TestClient,
    db_session: Session,
    user: dict[str, str],
    verified_ticket: Ticket,
    published_event: Event,
) -> None:
    now = datetime.now(UTC)
    other_ticket = make_verified_ticket(
        db_session,
        event_id=published_event.id,
        original_owner_id=user["id"],
        current_owner_id=user["id"],
    )
    oldest = make_listing(
        db_session,
        ticket=verified_ticket,
        status="CANCELLED",
        created_at=now - timedelta(minutes=3),
    )
    middle = make_listing(
        db_session, ticket=other_ticket, status="DRAFT", created_at=now - timedelta(minutes=2)
    )
    newest = make_listing(
        db_session, ticket=verified_ticket, status="ACTIVE", created_at=now - timedelta(minutes=1)
    )
    make_listing(db_session, status="ACTIVE")  # someone else's

    def ids(query: str = "") -> list[str]:
        response = api.get(f"{LISTINGS}/me{query}", headers=bearer(user["token"]))
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["items"]]

    assert ids() == [str(newest.id), str(middle.id), str(oldest.id)]
    assert ids("?status=CANCELLED") == [str(oldest.id)]
    assert ids(f"?ticket_id={verified_ticket.id}") == [str(newest.id), str(oldest.id)]


@pytest.mark.parametrize("query", ["?status=BOGUS", "?ticket_id=not-a-uuid"])
def test_bad_filters_are_422(api: TestClient, user: dict[str, str], query: str) -> None:
    response = api.get(f"{LISTINGS}/me{query}", headers=bearer(user["token"]))

    assert response.status_code == 422
