"""Who can reach a reservation, route by route. A guard test fails if a route that takes a
reservation ID is added without being listed here."""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.reservations.models import Reservation
from tests.integration.api import bearer, problem_code
from tests.integration.conftest import new_account
from tests.integration.factories import make_listing, make_reservation

pytestmark = pytest.mark.integration

GET = ("GET", "/api/v1/reservations/{reservation_id}")
CANCEL = ("POST", "/api/v1/reservations/{reservation_id}/cancel")
ROUTES = {GET, CANCEL}
UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"


def call(api: TestClient, route: tuple[str, str], reservation_id: object, token: str | None) -> Any:
    method, path = route
    return api.request(
        method,
        path.format(reservation_id=reservation_id),
        headers=bearer(token) if token else {},
    )


@pytest.fixture
def people(api: TestClient, db_session: Session) -> dict[str, dict[str, str]]:
    return {"buyer": new_account(api), "seller": new_account(api), "stranger": new_account(api)}


@pytest.fixture
def reservation(db_session: Session, people: dict[str, dict[str, str]]) -> Reservation:
    listing = make_listing(
        db_session, status="RESERVED", seller_id=uuid.UUID(people["seller"]["id"])
    )
    return make_reservation(
        db_session, listing=listing, buyer_id=uuid.UUID(people["buyer"]["id"]), status="CREATED"
    )


def test_every_reservation_route_is_covered(db_app: FastAPI) -> None:
    found = {
        (method.upper(), path)
        for path, operations in db_app.openapi()["paths"].items()
        if "{reservation_id}" in path
        for method in operations
    }
    assert found == ROUTES


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_stranger_gets_the_404_of_a_random_id(
    api: TestClient,
    people: dict[str, dict[str, str]],
    reservation: Reservation,
    route: tuple[str, str],
) -> None:
    token = people["stranger"]["token"]

    response = call(api, route, reservation.id, token)
    unknown = call(api, route, UNKNOWN_ID, token)

    assert response.status_code == unknown.status_code == 404
    assert problem_code(response) == "reservation-not-found"
    for key in ("type", "title", "detail"):
        assert response.json()[key] == unknown.json()[key]


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_guest_is_401(api: TestClient, reservation: Reservation, route: tuple[str, str]) -> None:
    assert call(api, route, reservation.id, None).status_code == 401


@pytest.mark.parametrize("who", ["buyer", "seller"])
def test_the_buyer_and_the_seller_read_it_and_neither_sees_who_the_buyer_is(
    api: TestClient, people: dict[str, dict[str, str]], reservation: Reservation, who: str
) -> None:
    response = call(api, GET, reservation.id, people[who]["token"])

    assert response.status_code == 200
    assert people["buyer"]["id"] not in response.text
    assert people["seller"]["id"] not in response.text


def test_an_admin_reads_it(
    api: TestClient, admin: dict[str, str], reservation: Reservation
) -> None:
    assert call(api, GET, reservation.id, admin["token"]).status_code == 200


def test_only_the_buyer_cancels(
    api: TestClient,
    admin: dict[str, str],
    people: dict[str, dict[str, str]],
    reservation: Reservation,
) -> None:
    for token in (people["seller"]["token"], admin["token"]):
        response = call(api, CANCEL, reservation.id, token)
        assert response.status_code == 403
        assert problem_code(response) == "buyer-only"
    assert call(api, CANCEL, reservation.id, people["buyer"]["token"]).status_code == 200
