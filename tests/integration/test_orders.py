"""Orders through the API: the Idempotency-Key, who may order what, and the order that ends with
its hold. Fifty clicks at once are in tests/concurrency/."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.idempotency import service as idempotency
from app.modules.idempotency.models import IdempotencyKeyRow
from app.modules.listings.models import Listing
from app.modules.orders.models import Order
from app.modules.reservations.models import Reservation
from app.modules.reservations.service import expire_reservations
from tests.integration.api import bearer, problem_code
from tests.integration.conftest import new_account
from tests.integration.factories import make_listing, make_order, make_reservation, make_user
from tests.integration.payments import held, ordered, post_order, reserve, with_key

pytestmark = pytest.mark.integration

UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"


def set_row(db: Session, model: type, row_id: object, **values: object) -> None:
    db.execute(update(model).where(model.id == row_id).values(**values))
    db.expire_all()


def audit(db: Session, entity_id: object) -> list[tuple[str, uuid.UUID | None]]:
    return [
        (row.action, row.actor_id)
        for row in db.scalars(
            select(AuditLog)
            .where(AuditLog.entity_id == uuid.UUID(str(entity_id)))
            .order_by(AuditLog.created_at)
        )
    ]


# ---------------------------------------------------------------- ordering


def test_an_order_copies_the_listings_price(api: TestClient, db_session: Session) -> None:
    buyer, reservation = held(api, db_session)

    response = post_order(api, reservation["id"], buyer)

    assert response.status_code == 201
    assert "Idempotent-Replayed" not in response.headers
    body = response.json()
    assert set(body) == {
        "id",
        "listing_id",
        "reservation_id",
        "amount_paise",
        "currency",
        "status",
        "created_at",
        "updated_at",
        "listing",
        "transfer",
    }
    assert body["transfer"] is None  # until it is paid
    assert (body["status"], body["currency"]) == ("CREATED", "INR")
    assert body["amount_paise"] == reservation["listing"]["asking_price_paise"]
    assert body["listing"] == reservation["listing"]
    assert audit(db_session, body["id"]) == [("order.created", uuid.UUID(buyer["id"]))]


def test_a_price_in_the_request_is_refused(api: TestClient, db_session: Session) -> None:
    buyer, reservation = held(api, db_session)
    response = api.post(
        "/api/v1/orders",
        json={"reservation_id": reservation["id"], "amount_paise": 1},
        headers=with_key(buyer),
    )
    assert response.status_code == 422


def test_the_hold_leads_to_its_order(api: TestClient, db_session: Session) -> None:
    buyer, order = ordered(api, db_session)
    hold = api.get(
        f"/api/v1/reservations/{order['reservation_id']}", headers=bearer(buyer["token"])
    ).json()
    mine = api.get("/api/v1/reservations/me", headers=bearer(buyer["token"])).json()["items"]
    assert hold["order_id"] == order["id"]
    assert [r["order_id"] for r in mine] == [order["id"]]


def test_someone_elses_hold_and_no_hold_are_the_same_404(
    api: TestClient, db_session: Session
) -> None:
    _, reservation = held(api, db_session)
    stranger = new_account(api)
    for reservation_id in (reservation["id"], UNKNOWN_ID):
        response = post_order(api, reservation_id, stranger)
        assert response.status_code == 404
        assert problem_code(response) == "reservation-not-found"


def test_the_seller_cannot_order_the_buyers_hold(api: TestClient, db_session: Session) -> None:
    seller = new_account(api)
    listing = make_listing(db_session, status="ACTIVE", seller_id=uuid.UUID(seller["id"]))
    reservation = reserve(api, listing.id, new_account(api))
    assert post_order(api, reservation["id"], seller).status_code == 404


def test_a_hold_that_ran_out_cannot_be_ordered(api: TestClient, db_session: Session) -> None:
    # The expiry job has not reached it yet, so it is still CREATED.
    buyer, reservation = held(api, db_session)
    set_row(
        db_session,
        Reservation,
        reservation["id"],
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    response = post_order(api, reservation["id"], buyer)

    assert response.status_code == 409
    assert problem_code(response) == "reservation-not-payable"
    assert response.json()["reason"] == "hold-ended"
    assert db_session.scalar(select(func.count()).select_from(Order)) == 0


@pytest.mark.parametrize("status", ["EXPIRED", "CANCELLED", "FAILED", "CONFIRMED"])
def test_a_hold_that_is_over_cannot_be_ordered(
    api: TestClient, db_session: Session, status: str
) -> None:
    buyer, reservation = held(api, db_session)
    set_row(db_session, Reservation, reservation["id"], status=status)
    response = post_order(api, reservation["id"], buyer)
    assert response.status_code == 409
    assert response.json()["reservation_status"] == status


def test_a_second_order_is_refused_and_leads_to_the_first(
    api: TestClient, db_session: Session
) -> None:
    buyer, order = ordered(api, db_session)

    response = post_order(api, order["reservation_id"], buyer)  # a new key: a second tab

    assert response.status_code == 409
    assert problem_code(response) == "order-exists"
    assert response.json()["order_id"] == order["id"]


# ---------------------------------------------------------------- the Idempotency-Key


def test_the_same_request_again_gets_the_same_answer(api: TestClient, db_session: Session) -> None:
    buyer, reservation = held(api, db_session)
    first = post_order(api, reservation["id"], buyer, "one-click")

    again = post_order(api, reservation["id"], buyer, "one-click")

    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json() == first.json()
    assert again.headers["Idempotent-Replayed"] == "true"
    assert db_session.scalar(select(func.count()).select_from(Order)) == 1


def test_the_answer_is_the_first_one_even_if_the_order_has_moved_on(
    api: TestClient, db_session: Session
) -> None:
    buyer, reservation = held(api, db_session)
    first = post_order(api, reservation["id"], buyer, "k")
    set_row(db_session, Order, first.json()["id"], status="CANCELLED")
    assert post_order(api, reservation["id"], buyer, "k").json()["status"] == "CREATED"


def test_spacing_does_not_make_it_another_request(api: TestClient, db_session: Session) -> None:
    buyer, reservation = held(api, db_session)
    post_order(api, reservation["id"], buyer, "k")
    again = api.post(
        "/api/v1/orders",
        content=f'{{ "reservation_id" :   "{reservation["id"].upper()}" }}',
        headers=with_key(buyer, "k") | {"Content-Type": "application/json"},
    )
    assert again.headers["Idempotent-Replayed"] == "true"


def test_the_same_key_for_something_else_is_a_bug_in_the_client(
    api: TestClient, db_session: Session
) -> None:
    buyer, reservation = held(api, db_session)
    post_order(api, reservation["id"], buyer, "k")

    response = post_order(api, UNKNOWN_ID, buyer, "k")

    assert response.status_code == 422
    assert problem_code(response) == "idempotency-key-reused"


def test_two_people_may_use_the_same_key(api: TestClient, db_session: Session) -> None:
    for _ in range(2):
        buyer, reservation = held(api, db_session)
        response = post_order(api, reservation["id"], buyer, "everyone")
        assert response.status_code == 201
        assert "Idempotent-Replayed" not in response.headers


def test_a_refused_request_does_not_use_up_its_key(api: TestClient, db_session: Session) -> None:
    buyer, reservation = held(api, db_session)
    set_row(db_session, Reservation, reservation["id"], status="PAYMENT_PENDING")
    assert post_order(api, reservation["id"], buyer, "k").status_code == 409
    set_row(db_session, Reservation, reservation["id"], status="CREATED")
    assert post_order(api, reservation["id"], buyer, "k").status_code == 201


@pytest.mark.parametrize(
    "key, code",
    [
        (None, "idempotency-key-required"),
        ("", "idempotency-key-required"),
        ("two words", "idempotency-key-invalid"),
        ("x" * 256, "idempotency-key-invalid"),
    ],
)
def test_a_missing_or_unusable_key_is_400(
    api: TestClient, db_session: Session, key: str | None, code: str
) -> None:
    buyer, reservation = held(api, db_session)
    headers = bearer(buyer["token"]) | ({} if key is None else {"Idempotency-Key": key})
    response = api.post(
        "/api/v1/orders", json={"reservation_id": reservation["id"]}, headers=headers
    )
    assert response.status_code == 400
    assert problem_code(response) == code


def test_the_longest_key_works(api: TestClient, db_session: Session) -> None:
    buyer, reservation = held(api, db_session)
    assert post_order(api, reservation["id"], buyer, "x" * 255).status_code == 201


def test_a_key_is_forgotten_after_a_day(api: TestClient, db_session: Session) -> None:
    buyer, reservation = held(api, db_session)
    post_order(api, reservation["id"], buyer, "k")
    db_session.execute(
        update(IdempotencyKeyRow).values(expires_at=func.now() - timedelta(seconds=1))
    )

    assert post_order(api, reservation["id"], buyer, "k").status_code == 409  # order-exists

    # And the table does not grow for ever.
    assert idempotency.delete_expired(db_session) == 1
    assert db_session.scalar(select(func.count()).select_from(IdempotencyKeyRow)) == 0


# ---------------------------------------------------------------- the order ends with its hold


def test_cancelling_the_hold_cancels_its_unpaid_order(api: TestClient, db_session: Session) -> None:
    buyer, order = ordered(api, db_session)

    response = api.post(
        f"/api/v1/reservations/{order['reservation_id']}/cancel", headers=bearer(buyer["token"])
    )

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(Order, uuid.UUID(order["id"])).status == "CANCELLED"
    assert db_session.get(Listing, uuid.UUID(order["listing_id"])).status == "ACTIVE"
    actor = uuid.UUID(buyer["id"])
    assert audit(db_session, order["id"]) == [("order.created", actor), ("order.cancelled", actor)]


def test_a_hold_that_expires_cancels_its_unpaid_order(api: TestClient, db_session: Session) -> None:
    _, order = ordered(api, db_session)

    later = datetime.now(UTC) + timedelta(minutes=11)
    assert expire_reservations(db_session, now=later) == (1, 1)

    db_session.expire_all()
    assert db_session.get(Order, uuid.UUID(order["id"])).status == "CANCELLED"
    assert audit(db_session, order["id"])[-1] == ("order.cancelled", None)


def test_the_ticket_can_be_held_and_ordered_again_afterwards(
    api: TestClient, db_session: Session
) -> None:
    buyer, order = ordered(api, db_session)
    api.post(
        f"/api/v1/reservations/{order['reservation_id']}/cancel", headers=bearer(buyer["token"])
    )
    again = reserve(api, order["listing_id"], buyer)
    assert post_order(api, again["id"], buyer).status_code == 201


def test_ordering_a_cancelled_hold_again_says_it_is_over(
    api: TestClient, db_session: Session
) -> None:
    buyer, order = ordered(api, db_session)
    api.post(
        f"/api/v1/reservations/{order['reservation_id']}/cancel", headers=bearer(buyer["token"])
    )
    response = post_order(api, order["reservation_id"], buyer)
    assert problem_code(response) == "reservation-not-payable"


# ---------------------------------------------------------------- reading


def test_my_orders_are_mine_newest_first(api: TestClient, db_session: Session) -> None:
    buyer = new_account(api)
    mine = [
        make_order(
            db_session,
            buyer_id=uuid.UUID(buyer["id"]),
            status=status,
            created_at=datetime.now(UTC) - timedelta(days=days),
        ).id
        for days, status in enumerate(["CREATED", "PAID", "CANCELLED"])
    ]
    make_order(db_session)

    page = api.get("/api/v1/orders/me", headers=bearer(buyer["token"])).json()
    paid = api.get("/api/v1/orders/me?status=PAID", headers=bearer(buyer["token"])).json()

    assert [item["id"] for item in page["items"]] == [str(i) for i in mine]
    assert [item["id"] for item in paid["items"]] == [str(mine[1])]


def test_my_sales_are_the_other_side(api: TestClient, db_session: Session) -> None:
    seller = new_account(api)
    listing = make_listing(db_session, status="RESERVED", seller_id=uuid.UUID(seller["id"]))
    order = make_order(db_session, reservation=make_reservation(db_session, listing=listing))

    sold = api.get("/api/v1/orders/me?role=seller", headers=bearer(seller["token"])).json()
    bought = api.get("/api/v1/orders/me", headers=bearer(seller["token"])).json()

    assert [item["id"] for item in sold["items"]] == [str(order.id)]
    assert bought["items"] == []


def test_my_orders_page_by_page(api: TestClient, db_session: Session) -> None:
    buyer = new_account(api)
    for _ in range(3):
        make_order(db_session, buyer_id=uuid.UUID(buyer["id"]))
    seen: list[str] = []
    cursor = ""
    for _ in range(3):
        page = api.get("/api/v1/orders/me?limit=1" + cursor, headers=bearer(buyer["token"])).json()
        seen += [item["id"] for item in page["items"]]
        cursor = f"&cursor={page['next_cursor']}"
    assert page["next_cursor"] is None
    assert len(set(seen)) == 3


@pytest.mark.parametrize("query", ["role=admin", "status=MAYBE"])
def test_an_unknown_filter_is_422(api: TestClient, query: str) -> None:
    account = new_account(api)
    response = api.get(f"/api/v1/orders/me?{query}", headers=bearer(account["token"]))
    assert response.status_code == 422


def test_someone_bought_from_never_learns_who(api: TestClient, db_session: Session) -> None:
    seller = new_account(api)
    buyer = make_user(db_session)
    listing = make_listing(db_session, status="RESERVED", seller_id=uuid.UUID(seller["id"]))
    order = make_order(
        db_session, reservation=make_reservation(db_session, listing=listing, buyer_id=buyer.id)
    )
    response = api.get(f"/api/v1/orders/{order.id}", headers=bearer(seller["token"]))
    assert response.status_code == 200
    assert str(buyer.id) not in response.text
    assert seller["id"] not in response.text
