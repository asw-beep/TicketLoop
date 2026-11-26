"""Who can reach an order and a payment, route by route. A guard test fails if a route that takes
either ID is added without being listed here."""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.orders.models import Order
from app.modules.payments.models import Payment
from tests.integration.api import bearer, problem_code
from tests.integration.conftest import new_account
from tests.integration.factories import (
    make_listing,
    make_order,
    make_payment,
    make_reservation,
)
from tests.integration.payments import (
    Fakepay,
    fakepay,  # noqa: F401
    post_intent,
)

pytestmark = pytest.mark.integration

GET_ORDER = ("GET", "/api/v1/orders/{order_id}")
GET_PAYMENT = ("GET", "/api/v1/payments/{payment_id}")
ROUTES = {GET_ORDER, GET_PAYMENT}
UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"

People = dict[str, dict[str, str]]


@pytest.fixture
def people(api: TestClient) -> People:
    return {who: new_account(api) for who in ("buyer", "seller", "stranger")}


@pytest.fixture
def order(db_session: Session, people: People) -> Order:
    listing = make_listing(
        db_session, status="RESERVED", seller_id=uuid.UUID(people["seller"]["id"])
    )
    reservation = make_reservation(
        db_session, listing=listing, buyer_id=uuid.UUID(people["buyer"]["id"])
    )
    return make_order(db_session, reservation=reservation)


@pytest.fixture
def payment(db_session: Session, order: Order) -> Payment:
    return make_payment(db_session, order=order, status="PENDING")


def call(
    api: TestClient, route: tuple[str, str], order_id: object, payment_id: object, token: str | None
) -> Any:
    method, path = route
    url = path.format(order_id=order_id, payment_id=payment_id)
    return api.request(method, url, headers=bearer(token) if token else {})


def test_every_route_is_covered(db_app: FastAPI) -> None:
    found = {
        (method.upper(), path)
        for path, operations in db_app.openapi()["paths"].items()
        if "{order_id}" in path or "{payment_id}" in path
        for method in operations
    }
    assert found == ROUTES


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_stranger_gets_the_404_of_a_random_id(
    api: TestClient, people: People, order: Order, payment: Payment, route: tuple[str, str]
) -> None:
    token = people["stranger"]["token"]

    response = call(api, route, order.id, payment.id, token)
    unknown = call(api, route, UNKNOWN_ID, UNKNOWN_ID, token)

    assert response.status_code == unknown.status_code == 404
    for key in ("type", "title", "detail"):
        assert response.json()[key] == unknown.json()[key]


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_guest_is_401(
    api: TestClient, order: Order, payment: Payment, route: tuple[str, str]
) -> None:
    assert call(api, route, order.id, payment.id, None).status_code == 401


def test_guests_cannot_order_list_or_pay(api: TestClient, order: Order) -> None:
    key = {"Idempotency-Key": "k"}
    for response in (
        api.get("/api/v1/orders/me"),
        api.post("/api/v1/orders", json={"reservation_id": str(order.reservation_id)}, headers=key),
        api.post("/api/v1/payments/intents", json={"order_id": str(order.id)}, headers=key),
    ):
        assert response.status_code == 401


@pytest.mark.parametrize("who", ["buyer", "seller"])
def test_the_buyer_and_the_seller_read_the_order(
    api: TestClient, people: People, order: Order, who: str
) -> None:
    response = call(api, GET_ORDER, order.id, None, people[who]["token"])
    assert response.status_code == 200
    assert people["buyer"]["id"] not in response.text
    assert people["seller"]["id"] not in response.text


def test_the_buyer_reads_the_payment_and_the_seller_does_not(
    api: TestClient, people: People, payment: Payment
) -> None:
    buyer = call(api, GET_PAYMENT, None, payment.id, people["buyer"]["token"])
    assert buyer.status_code == 200
    assert set(buyer.json()) == {
        "id",
        "order_id",
        "provider",
        "status",
        "amount_paise",
        "currency",
        "failure_reason",
        "created_at",
        "updated_at",
    }
    seller = call(api, GET_PAYMENT, None, payment.id, people["seller"]["token"])
    assert (seller.status_code, problem_code(seller)) == (404, "payment-not-found")


def test_an_admin_reads_both(
    api: TestClient, admin: dict[str, str], order: Order, payment: Payment
) -> None:
    for route in ROUTES:
        assert call(api, route, order.id, payment.id, admin["token"]).status_code == 200


def test_only_the_buyer_pays(
    api: TestClient,
    admin: dict[str, str],
    people: People,
    order: Order,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    for account in (people["seller"], admin):
        response = post_intent(api, order.id, account)
        assert (response.status_code, problem_code(response)) == (403, "buyer-only")
    response = post_intent(api, order.id, people["stranger"])
    assert (response.status_code, problem_code(response)) == (404, "order-not-found")
    assert fakepay.calls == []
    assert post_intent(api, order.id, people["buyer"]).status_code == 201
