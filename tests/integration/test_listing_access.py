"""Who can reach a listing, route by route.

Every route that takes a listing ID is listed below, and a guard test fails if a new one is
added without being listed, so it cannot skip these checks. A listing that is not on sale is
the same 404 as an unknown ID to everyone but its seller and admins; an ACTIVE listing can be
read by anyone, but only its seller acts on it.
"""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.events.models import Event
from tests.integration.api import bearer, problem_code
from tests.integration.conftest import new_account
from tests.integration.factories import make_listing, make_verified_ticket

pytestmark = pytest.mark.integration

GET_LISTING = ("GET", "/api/v1/listings/{listing_id}")
SELLER_ROUTES = {
    ("PATCH", "/api/v1/listings/{listing_id}"),
    ("POST", "/api/v1/listings/{listing_id}/submit"),
    ("DELETE", "/api/v1/listings/{listing_id}"),
    ("GET", "/api/v1/listings/{listing_id}/history"),
}
# For anyone but the seller. test_reservations.py covers what each gets.
BUYER_ROUTES = {("POST", "/api/v1/listings/{listing_id}/reservations")}
ADMIN_ROUTES = {
    ("POST", "/api/v1/admin/listings/{listing_id}/approve"),
    ("POST", "/api/v1/admin/listings/{listing_id}/reject"),
}
LISTING_ROUTES = {GET_LISTING} | SELLER_ROUTES | BUYER_ROUTES | ADMIN_ROUTES
BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("PATCH", "/api/v1/listings/{listing_id}"): {"asking_price_paise": 100},
    ("POST", "/api/v1/admin/listings/{listing_id}/reject"): {"reason": "x"},
}
UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"


def call(
    api: TestClient, route: tuple[str, str], listing_id: str, account: dict[str, str] | None
) -> Any:
    method, path = route
    headers = bearer(account["token"]) if account else {}
    body = BODIES.get(route)
    return api.request(
        method,
        path.format(listing_id=listing_id),
        headers=headers,
        **({"json": body} if body is not None else {}),
    )


def comparable(response: Any) -> dict[str, Any]:
    """The parts of a problem body that must not differ between "missing" and "not yours"."""
    body = response.json()
    return {key: body[key] for key in ("type", "title", "status", "detail")}


def alices_listing(api: TestClient, db: Session, event: Event, status: str) -> str:
    alice = new_account(api)
    ticket = make_verified_ticket(
        db, event_id=event.id, original_owner_id=alice["id"], current_owner_id=alice["id"]
    )
    return str(make_listing(db, ticket=ticket, status=status).id)


def test_every_listing_route_is_covered(db_app: FastAPI) -> None:
    # Read from the OpenAPI schema, as test_ticket_access.py does: included routes are not
    # listed flat in app.routes, and the schema is the public description of every route.
    found = {
        (method.upper(), path)
        for path, operations in db_app.openapi()["paths"].items()
        if "{listing_id}" in path
        for method in operations
    }

    assert found == LISTING_ROUTES


@pytest.mark.parametrize("route", sorted(LISTING_ROUTES))
def test_another_user_cannot_reach_a_draft(
    api: TestClient, db_session: Session, published_event: Event, route: tuple[str, str]
) -> None:
    listing_id = alices_listing(api, db_session, published_event, "DRAFT")
    bob = new_account(api)

    response = call(api, route, listing_id, bob)

    if route in ADMIN_ROUTES:
        assert response.status_code == 403
        return
    assert response.status_code == 404
    assert problem_code(response) == "listing-not-found"
    assert comparable(response) == comparable(call(api, route, UNKNOWN_ID, bob))


@pytest.mark.parametrize("route", sorted(LISTING_ROUTES))
def test_a_guest_cannot_reach_a_draft(
    api: TestClient, db_session: Session, published_event: Event, route: tuple[str, str]
) -> None:
    listing_id = alices_listing(api, db_session, published_event, "DRAFT")

    response = call(api, route, listing_id, None)

    assert response.status_code == (404 if route == GET_LISTING else 401)


@pytest.mark.parametrize("route", sorted({GET_LISTING} | SELLER_ROUTES))
def test_another_user_reads_an_active_listing_but_cannot_act_on_it(
    api: TestClient, db_session: Session, published_event: Event, route: tuple[str, str]
) -> None:
    listing_id = alices_listing(api, db_session, published_event, "ACTIVE")
    bob = new_account(api)

    response = call(api, route, listing_id, bob)

    if route == GET_LISTING:
        assert response.status_code == 200
    else:
        assert response.status_code == 404
        assert problem_code(response) == "listing-not-found"
