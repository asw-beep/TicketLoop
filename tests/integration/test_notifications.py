"""Notifications: one per person per event, however often it is delivered, and only yours."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.notifications.models import Notification
from app.modules.notifications.service import notify
from tests.integration.api import bearer, problem_code
from tests.integration.conftest import new_account

pytestmark = pytest.mark.integration


def mine(db: Session, user_id: str | uuid.UUID) -> list[Notification]:
    return list(
        db.scalars(
            select(Notification)
            .where(Notification.user_id == uuid.UUID(str(user_id)))
            .order_by(Notification.created_at, Notification.id)
        )
    )


def sale(buyer: str, seller: str) -> dict[str, str]:
    return {
        "order_id": str(uuid.uuid4()),
        "listing_id": str(uuid.uuid4()),
        "payment_id": str(uuid.uuid4()),
        "buyer_id": buyer,
        "seller_id": seller,
    }


def test_a_sale_tells_the_buyer_and_the_seller_once(api: TestClient, db_session: Session) -> None:
    buyer, seller = new_account(api), new_account(api)
    event_id, payload = uuid.uuid4(), sale(buyer["id"], seller["id"])

    assert notify(db_session, event_id, "payment.succeeded", payload) == 2
    assert notify(db_session, event_id, "payment.succeeded", payload) == 0

    (to_buyer,) = mine(db_session, buyer["id"])
    (to_seller,) = mine(db_session, seller["id"])
    assert (to_buyer.notification_type, to_seller.notification_type) == (
        "payment_succeeded",
        "ticket_sold",
    )
    # Things to link to, never the other person.
    assert to_buyer.payload == {
        "order_id": payload["order_id"],
        "listing_id": payload["listing_id"],
    }
    assert to_buyer.source_event_id == event_id


@pytest.mark.parametrize(
    ("event_type", "buyer_kind", "seller_kind"),
    [
        ("payment.failed", "payment_failed", None),
        ("reservation.expired", "hold_expired", None),
        ("transfer.completed", "ticket_received", "ticket_handed_over"),
        ("transfer.failed", "transfer_needs_review", "transfer_needs_review"),
        ("transfer.initiated", None, None),
    ],
)
def test_who_hears_about_what(
    api: TestClient,
    db_session: Session,
    event_type: str,
    buyer_kind: str | None,
    seller_kind: str | None,
) -> None:
    buyer, seller = new_account(api), new_account(api)

    notify(db_session, uuid.uuid4(), event_type, sale(buyer["id"], seller["id"]))

    assert [n.notification_type for n in mine(db_session, buyer["id"])] == (
        [buyer_kind] if buyer_kind else []
    )
    assert [n.notification_type for n in mine(db_session, seller["id"])] == (
        [seller_kind] if seller_kind else []
    )


def test_your_notifications_newest_first_and_only_yours(
    api: TestClient, db_session: Session
) -> None:
    buyer, seller = new_account(api), new_account(api)
    notify(db_session, uuid.uuid4(), "payment.succeeded", sale(buyer["id"], seller["id"]))
    notify(db_session, uuid.uuid4(), "transfer.completed", sale(buyer["id"], seller["id"]))

    page = api.get("/api/v1/notifications", headers=bearer(buyer["token"])).json()

    assert [n["notification_type"] for n in page["items"]] == [
        "ticket_received",
        "payment_succeeded",
    ]
    assert set(page["items"][0]) == {"id", "notification_type", "payload", "read_at", "created_at"}
    assert page["next_cursor"] is None
    assert (
        api.get("/api/v1/notifications", headers=bearer(new_account(api)["token"])).json()["items"]
        == []
    )


def test_reading_one_is_remembered_and_harmless_to_repeat(
    api: TestClient, db_session: Session
) -> None:
    buyer, seller = new_account(api), new_account(api)
    notify(db_session, uuid.uuid4(), "payment.succeeded", sale(buyer["id"], seller["id"]))
    notify(db_session, uuid.uuid4(), "transfer.completed", sale(buyer["id"], seller["id"]))
    newest, older = api.get("/api/v1/notifications", headers=bearer(buyer["token"])).json()["items"]

    first = api.patch(f"/api/v1/notifications/{older['id']}/read", headers=bearer(buyer["token"]))
    again = api.patch(f"/api/v1/notifications/{older['id']}/read", headers=bearer(buyer["token"]))

    assert first.status_code == again.status_code == 200
    assert first.json()["read_at"] is not None
    assert again.json()["read_at"] == first.json()["read_at"]
    unread = api.get(
        "/api/v1/notifications?unread_only=true", headers=bearer(buyer["token"])
    ).json()
    assert [n["id"] for n in unread["items"]] == [newest["id"]]


def test_someone_elses_notification_is_not_found(api: TestClient, db_session: Session) -> None:
    buyer, seller = new_account(api), new_account(api)
    notify(db_session, uuid.uuid4(), "payment.succeeded", sale(buyer["id"], seller["id"]))
    (theirs,) = api.get("/api/v1/notifications", headers=bearer(buyer["token"])).json()["items"]

    for path in (
        f"/api/v1/notifications/{theirs['id']}/read",
        f"/api/v1/notifications/{uuid.uuid4()}/read",
    ):
        response = api.patch(path, headers=bearer(seller["token"]))
        assert response.status_code == 404
        assert problem_code(response) == "notification-not-found"


def test_notifications_need_a_login(api: TestClient) -> None:
    assert api.get("/api/v1/notifications").status_code == 401
