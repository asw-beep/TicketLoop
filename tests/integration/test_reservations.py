"""Reserving, reading and cancelling through the API. Races are in tests/concurrency/."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.listings.models import Listing
from app.modules.reservations.models import Reservation
from tests.integration.api import bearer, problem_code
from tests.integration.conftest import new_account
from tests.integration.factories import make_listing, make_reservation

pytestmark = pytest.mark.integration

UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"


def reserve(api: TestClient, listing_id: object, account: dict[str, str]):
    return api.post(f"/api/v1/listings/{listing_id}/reservations", headers=bearer(account["token"]))


def cancel(api: TestClient, reservation_id: object, account: dict[str, str]):
    return api.post(
        f"/api/v1/reservations/{reservation_id}/cancel", headers=bearer(account["token"])
    )


def actions(db: Session, entity_type: str, entity_id: object) -> list[str]:
    return list(
        db.scalars(
            select(AuditLog.action)
            .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
            .order_by(AuditLog.created_at)
        )
    )


def set_row(db: Session, model: type, row_id: object, **values: object) -> None:
    db.execute(update(model).where(model.id == row_id).values(**values))
    db.expire_all()


# ---------------------------------------------------------------- reserving


def test_reserving_holds_the_listing_for_ten_minutes(api: TestClient, db_session: Session) -> None:
    listing = make_listing(db_session, status="ACTIVE")
    buyer = new_account(api)

    before = datetime.now(UTC)
    response = reserve(api, listing.id, buyer)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "listing_id", "status", "expires_at", "created_at", "listing"}
    assert body["listing_id"] == str(listing.id)
    assert body["status"] == "CREATED"
    held_for = datetime.fromisoformat(body["expires_at"]) - before
    assert timedelta(minutes=9) < held_for <= timedelta(minutes=10, seconds=5)
    assert set(body["listing"]) == {
        "id",
        "event",
        "ticket_category",
        "seat_details",
        "asking_price_paise",
        "original_price_paise",
    }
    db_session.expire_all()
    assert db_session.get(Listing, listing.id).status == "RESERVED"


def test_reserving_writes_one_audit_row_for_each_thing_that_changed(
    api: TestClient, db_session: Session
) -> None:
    listing = make_listing(db_session, status="ACTIVE")
    buyer = new_account(api)

    reservation_id = reserve(api, listing.id, buyer).json()["id"]

    assert actions(db_session, "listing", listing.id) == ["listing.reserved"]
    assert actions(db_session, "reservation", reservation_id) == ["reservation.created"]
    row = db_session.scalar(select(AuditLog).where(AuditLog.entity_id == listing.id))
    assert row.actor_id == uuid.UUID(buyer["id"])
    assert row.details["reservation_id"] == reservation_id


def test_reserving_again_returns_the_hold_you_have(api: TestClient, db_session: Session) -> None:
    listing = make_listing(db_session, status="ACTIVE")
    buyer = new_account(api)
    first = reserve(api, listing.id, buyer)

    again = reserve(api, listing.id, buyer)

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json()
    assert actions(db_session, "listing", listing.id) == ["listing.reserved"]


def test_someone_else_gets_409_reserved(api: TestClient, db_session: Session) -> None:
    listing = make_listing(db_session, status="ACTIVE")
    reserve(api, listing.id, new_account(api))

    response = reserve(api, listing.id, new_account(api))

    assert response.status_code == 409
    assert problem_code(response) == "listing-not-available"
    assert response.json()["reason"] == "reserved"


@pytest.mark.parametrize("status, reason", [("SOLD", "sold"), ("EXPIRED", "expired")])
def test_a_listing_that_is_over_is_409_with_why(
    api: TestClient, db_session: Session, status: str, reason: str
) -> None:
    listing = make_listing(db_session, status=status)

    response = reserve(api, listing.id, new_account(api))

    assert response.status_code == 409
    assert response.json()["reason"] == reason


def test_an_active_listing_past_its_end_cannot_be_reserved(
    api: TestClient, db_session: Session
) -> None:
    # The expiry job has not reached it yet, so it is still ACTIVE.
    listing = make_listing(
        db_session, status="ACTIVE", expires_at=datetime.now(UTC) - timedelta(minutes=5)
    )

    response = reserve(api, listing.id, new_account(api))

    assert response.status_code == 409
    assert response.json()["reason"] == "expired"
    db_session.expire_all()
    assert db_session.get(Listing, listing.id).status == "ACTIVE"
    assert actions(db_session, "listing", listing.id) == []


@pytest.mark.parametrize("status", ["DRAFT", "PENDING_VERIFICATION", "REJECTED", "CANCELLED"])
def test_a_listing_never_on_sale_to_you_is_404(
    api: TestClient, db_session: Session, status: str
) -> None:
    listing = make_listing(db_session, status=status)
    buyer = new_account(api)

    response = reserve(api, listing.id, buyer)
    unknown = reserve(api, UNKNOWN_ID, buyer)

    assert response.status_code == unknown.status_code == 404
    for key in ("type", "title", "detail"):
        assert response.json()[key] == unknown.json()[key]


def test_the_seller_cannot_reserve_their_own_listing(api: TestClient, db_session: Session) -> None:
    seller = new_account(api)
    listing = make_listing(db_session, status="ACTIVE", seller_id=uuid.UUID(seller["id"]))

    response = reserve(api, listing.id, seller)

    assert response.status_code == 403
    assert problem_code(response) == "own-listing-reservation"
    db_session.expire_all()
    assert db_session.get(Listing, listing.id).status == "ACTIVE"


def test_reserving_needs_a_login(api: TestClient, db_session: Session) -> None:
    listing = make_listing(db_session, status="ACTIVE")
    assert api.post(f"/api/v1/listings/{listing.id}/reservations").status_code == 401


def test_a_hold_that_ran_out_is_not_a_hold(api: TestClient, db_session: Session) -> None:
    # Past its end, and the expiry loop has not reached it yet.
    listing = make_listing(db_session, status="ACTIVE")
    buyer = new_account(api)
    reservation_id = reserve(api, listing.id, buyer).json()["id"]
    set_row(
        db_session, Reservation, reservation_id, expires_at=datetime.now(UTC) - timedelta(minutes=5)
    )

    mine = reserve(api, listing.id, buyer)
    theirs = reserve(api, listing.id, new_account(api))

    assert (mine.status_code, mine.json()["reason"]) == (409, "hold-ended")
    assert (theirs.status_code, theirs.json()["reason"]) == (409, "reserved")


def test_a_second_live_row_is_refused_even_if_the_listing_says_active(
    api: TestClient, db_session: Session
) -> None:
    # It cannot happen through the API; the unique index is the net under the code.
    listing = make_listing(db_session, status="ACTIVE")
    make_reservation(db_session, listing=listing, status="CREATED")

    response = reserve(api, listing.id, new_account(api))

    assert response.status_code == 409
    assert problem_code(response) == "listing-not-available"
    db_session.expire_all()
    assert db_session.get(Listing, listing.id).status == "ACTIVE"


# ---------------------------------------------------------------- cancelling


def test_cancelling_puts_the_ticket_back_on_sale(api: TestClient, db_session: Session) -> None:
    listing = make_listing(db_session, status="ACTIVE")
    buyer = new_account(api)
    reservation_id = reserve(api, listing.id, buyer).json()["id"]

    response = cancel(api, reservation_id, buyer)

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    db_session.expire_all()
    assert db_session.get(Listing, listing.id).status == "ACTIVE"
    assert actions(db_session, "reservation", reservation_id) == [
        "reservation.created",
        "reservation.cancelled",
    ]
    assert actions(db_session, "listing", listing.id) == ["listing.reserved", "listing.released"]
    assert reserve(api, listing.id, new_account(api)).status_code == 201


def test_cancelling_after_the_listings_own_end_expires_it(
    api: TestClient, db_session: Session
) -> None:
    listing = make_listing(db_session, status="ACTIVE")
    buyer = new_account(api)
    reservation_id = reserve(api, listing.id, buyer).json()["id"]
    set_row(db_session, Listing, listing.id, expires_at=datetime.now(UTC) - timedelta(minutes=5))

    cancel(api, reservation_id, buyer)

    db_session.expire_all()
    assert db_session.get(Listing, listing.id).status == "EXPIRED"


@pytest.mark.parametrize(
    "status", ["PAYMENT_PENDING", "CONFIRMED", "EXPIRED", "FAILED", "CANCELLED"]
)
def test_only_an_unpaid_hold_can_be_cancelled(
    api: TestClient, db_session: Session, status: str
) -> None:
    buyer = new_account(api)
    listing = make_listing(db_session, status="RESERVED")
    reservation = make_reservation(
        db_session, listing=listing, buyer_id=uuid.UUID(buyer["id"]), status=status
    )

    response = cancel(api, reservation.id, buyer)

    assert response.status_code == 409
    assert problem_code(response) == "reservation-not-cancellable"
    db_session.expire_all()
    assert db_session.get(Listing, listing.id).status == "RESERVED"


# ---------------------------------------------------------------- reading


def test_my_reservations_live_ones_first_then_newest(api: TestClient, db_session: Session) -> None:
    buyer = new_account(api)
    start = datetime.now(UTC) - timedelta(days=1)
    statuses = ["EXPIRED", "CREATED", "CANCELLED", "PAYMENT_PENDING", "CONFIRMED"]
    for minutes, status in enumerate(statuses):
        make_reservation(
            db_session,
            buyer_id=uuid.UUID(buyer["id"]),
            status=status,
            created_at=start + timedelta(minutes=minutes),
        )
    make_reservation(db_session, status="CREATED")  # someone else's

    seen, cursor = [], None
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        page = api.get(
            "/api/v1/reservations/me", params=params, headers=bearer(buyer["token"])
        ).json()
        seen += [r["status"] for r in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == ["PAYMENT_PENDING", "CREATED", "CONFIRMED", "CANCELLED", "EXPIRED"]


def test_my_reservations_refuses_a_bad_cursor(api: TestClient) -> None:
    response = api.get(
        "/api/v1/reservations/me",
        params={"cursor": "nonsense"},
        headers=bearer(new_account(api)["token"]),
    )
    assert response.status_code == 400
    assert problem_code(response) == "bad-cursor"


def test_the_sellers_history_says_a_buyer_and_never_which(
    api: TestClient, db_session: Session
) -> None:
    seller, buyer = new_account(api), new_account(api)
    listing = make_listing(db_session, status="ACTIVE", seller_id=uuid.UUID(seller["id"]))
    reservation_id = reserve(api, listing.id, buyer).json()["id"]
    cancel(api, reservation_id, buyer)

    response = api.get(f"/api/v1/listings/{listing.id}/history", headers=bearer(seller["token"]))

    entries = response.json()["items"]
    assert [(e["action"], e["actor_role"]) for e in entries] == [
        ("listing.reserved", "buyer"),
        ("listing.released", "buyer"),
    ]
    assert entries[1]["details"] == {"reason": "buyer_cancelled"}
    assert buyer["id"] not in response.text
    assert reservation_id not in response.text
