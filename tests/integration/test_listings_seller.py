"""The seller's actions on a listing: edit, submit, cancel. And who else may do any of them.

The seller check runs first: on their own listing an admin is treated as the seller. Admins
cancel other people's ACTIVE listings; they never edit or submit them (403 seller-only).
Anyone else gets 404, even for an ACTIVE listing they can see.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.events.models import Event
from app.modules.listings.models import Listing
from app.modules.tickets.models import Ticket
from tests.integration.api import bearer, problem_code
from tests.integration.conftest import new_account
from tests.integration.factories import make_listing, make_verified_ticket

pytestmark = pytest.mark.integration

LISTINGS = "/api/v1/listings"
MakeListing = Callable[..., Listing]


@pytest.fixture
def listing_of(db_session: Session, verified_ticket: Ticket) -> MakeListing:
    """`user`'s listing of `verified_ticket` at ₹2,000, in the status asked for."""

    def make(status: str = "DRAFT", **fields: Any) -> Listing:
        return make_listing(
            db_session,
            ticket=verified_ticket,
            status=status,
            **({"asking_price_paise": 200_000} | fields),
        )

    return make


def actions(db: Session, listing: Listing) -> list[AuditLog]:
    return list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.entity_type == "listing", AuditLog.entity_id == listing.id)
            .order_by(AuditLog.created_at, AuditLog.id)
        )
    )


def patch(api: TestClient, token: str, listing: Listing, body: dict[str, Any]) -> Any:
    return api.patch(f"{LISTINGS}/{listing.id}", json=body, headers=bearer(token))


def submit(api: TestClient, token: str, listing: Listing) -> Any:
    return api.post(f"{LISTINGS}/{listing.id}/submit", headers=bearer(token))


def cancel(api: TestClient, token: str, listing: Listing) -> Any:
    return api.delete(f"{LISTINGS}/{listing.id}", headers=bearer(token))


# ---------------------------------------------------------------- edit


@pytest.mark.parametrize("status", ["DRAFT", "PENDING_VERIFICATION", "ACTIVE"])
def test_price_and_expiry_change_while_the_listing_is_open(
    api: TestClient,
    user: dict[str, str],
    listing_of: MakeListing,
    published_event: Event,
    status: str,
) -> None:
    listing = listing_of(status)
    new_expiry = published_event.starts_at - timedelta(days=1)

    response = patch(
        api,
        user["token"],
        listing,
        {"asking_price_paise": 150_000, "expires_at": new_expiry.isoformat()},
    )

    assert response.status_code == 200, response.text
    assert response.json()["asking_price_paise"] == 150_000
    assert datetime.fromisoformat(response.json()["expires_at"]) == new_expiry


def test_an_active_listing_can_get_cheaper(
    api: TestClient, user: dict[str, str], listing_of: MakeListing
) -> None:
    response = patch(api, user["token"], listing_of("ACTIVE"), {"asking_price_paise": 199_800})

    assert response.status_code == 200
    assert response.json()["asking_price_paise"] == 199_800


def test_an_active_listing_cannot_get_dearer(
    api: TestClient, user: dict[str, str], listing_of: MakeListing
) -> None:
    response = patch(api, user["token"], listing_of("ACTIVE"), {"asking_price_paise": 200_001})

    assert response.status_code == 409
    assert problem_code(response) == "price-increase-not-allowed"


def test_a_price_above_face_value_is_422(
    api: TestClient, user: dict[str, str], listing_of: MakeListing
) -> None:
    response = patch(api, user["token"], listing_of(), {"asking_price_paise": 250_001})

    assert response.status_code == 422
    assert problem_code(response) == "price-out-of-range"


@pytest.mark.parametrize("status", ["REJECTED", "CANCELLED", "EXPIRED"])
def test_closed_listings_cannot_be_edited(
    api: TestClient, user: dict[str, str], listing_of: MakeListing, status: str
) -> None:
    response = patch(api, user["token"], listing_of(status), {"asking_price_paise": 150_000})

    assert response.status_code == 409
    assert problem_code(response) == "listing-not-editable"


@pytest.mark.parametrize(
    "body",
    [{}, {"asking_price_paise": None}, {"expires_at": None}],
    ids=["empty", "null price", "null expiry"],
)
def test_an_empty_or_null_edit_is_422(
    api: TestClient, user: dict[str, str], listing_of: MakeListing, body: dict[str, Any]
) -> None:
    response = patch(api, user["token"], listing_of(), body)

    assert response.status_code == 422


def test_the_same_price_again_changes_nothing(
    api: TestClient, db_session: Session, user: dict[str, str], listing_of: MakeListing
) -> None:
    listing = listing_of("ACTIVE")

    response = patch(api, user["token"], listing, {"asking_price_paise": 200_000})

    assert response.status_code == 200
    assert actions(db_session, listing) == []


def test_price_and_expiry_changes_are_audited(
    api: TestClient,
    db_session: Session,
    user: dict[str, str],
    listing_of: MakeListing,
    published_event: Event,
) -> None:
    listing = listing_of()
    old_expiry = listing.expires_at
    new_expiry = published_event.starts_at - timedelta(days=2)

    patch(
        api,
        user["token"],
        listing,
        {"asking_price_paise": 150_000, "expires_at": new_expiry.isoformat()},
    )

    rows = {row.action: row for row in actions(db_session, listing)}
    assert set(rows) == {"listing.price_changed", "listing.expiry_changed"}
    assert rows["listing.price_changed"].details == {
        "old_price_paise": 200_000,
        "new_price_paise": 150_000,
    }
    assert rows["listing.expiry_changed"].details == {
        "old_expires_at": old_expiry.isoformat(),
        "new_expires_at": new_expiry.isoformat(),
    }
    assert {str(row.actor_id) for row in rows.values()} == {user["id"]}


# ---------------------------------------------------------------- submit


def test_submit_sends_a_draft_for_review(
    api: TestClient, db_session: Session, user: dict[str, str], listing_of: MakeListing
) -> None:
    listing = listing_of()

    response = submit(api, user["token"], listing)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "PENDING_VERIFICATION"
    assert [row.action for row in actions(db_session, listing)] == ["listing.submitted"]


def test_only_a_draft_can_be_submitted(
    api: TestClient, user: dict[str, str], listing_of: MakeListing
) -> None:
    response = submit(api, user["token"], listing_of("PENDING_VERIFICATION"))

    assert response.status_code == 409
    assert problem_code(response) == "listing-not-draft"


def test_submit_checks_the_event_again(
    api: TestClient,
    db_session: Session,
    user: dict[str, str],
    listing_of: MakeListing,
    published_event: Event,
) -> None:
    listing = listing_of()
    published_event.status = "CANCELLED"
    db_session.flush()

    response = submit(api, user["token"], listing)

    assert response.status_code == 409
    assert problem_code(response) == "event-not-open"


def test_submit_refuses_an_expiry_that_has_passed(
    api: TestClient, user: dict[str, str], listing_of: MakeListing
) -> None:
    listing = listing_of(expires_at=datetime.now(UTC) - timedelta(minutes=1))

    response = submit(api, user["token"], listing)

    assert response.status_code == 422
    assert problem_code(response) == "bad-expiry"


def test_if_the_audit_write_fails_the_status_change_does_not_commit(
    db_app: FastAPI,
    db_session: Session,
    user: dict[str, str],
    listing_of: MakeListing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = listing_of()

    def broken_record(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the audit write failed")

    monkeypatch.setattr("app.modules.listings.state.record", broken_record)
    with TestClient(db_app, raise_server_exceptions=False) as client:
        response = submit(client, user["token"], listing)

    assert response.status_code == 500
    db_session.expire_all()
    assert db_session.get(Listing, listing.id).status == "DRAFT"


# ---------------------------------------------------------------- cancel


@pytest.mark.parametrize("status", ["DRAFT", "PENDING_VERIFICATION", "ACTIVE"])
def test_the_seller_cancels_an_open_listing(
    api: TestClient,
    db_session: Session,
    user: dict[str, str],
    listing_of: MakeListing,
    status: str,
) -> None:
    listing = listing_of(status)

    response = cancel(api, user["token"], listing)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "CANCELLED"
    (row,) = actions(db_session, listing)
    assert row.action == "listing.cancelled"
    assert row.details["by"] == "seller"


@pytest.mark.parametrize("status", ["RESERVED", "SOLD", "EXPIRED"])
def test_reserved_and_closed_listings_cannot_be_cancelled(
    api: TestClient, user: dict[str, str], listing_of: MakeListing, status: str
) -> None:
    response = cancel(api, user["token"], listing_of(status))

    assert response.status_code == 409
    assert problem_code(response) == "listing-not-cancellable"
    assert status in response.json()["detail"]


def test_an_admin_cancels_an_active_listing(
    api: TestClient, db_session: Session, admin: dict[str, str], listing_of: MakeListing
) -> None:
    listing = listing_of("ACTIVE")

    response = cancel(api, admin["token"], listing)

    assert response.status_code == 200
    (row,) = actions(db_session, listing)
    assert row.details["by"] == "admin"
    assert str(row.actor_id) == admin["id"]


def test_an_admin_cannot_cancel_a_pending_listing(
    api: TestClient, admin: dict[str, str], listing_of: MakeListing
) -> None:
    response = cancel(api, admin["token"], listing_of("PENDING_VERIFICATION"))

    assert response.status_code == 409
    assert problem_code(response) == "listing-not-cancellable"


# ---------------------------------------------------------------- who


SELLER_ACTIONS = {
    "edit": lambda api, token, listing: patch(api, token, listing, {"asking_price_paise": 1000}),
    "submit": submit,
    "cancel": cancel,
}


@pytest.mark.parametrize("action", sorted(SELLER_ACTIONS))
def test_another_user_gets_404_even_for_an_active_listing(
    api: TestClient, listing_of: MakeListing, action: str
) -> None:
    stranger = new_account(api)

    response = SELLER_ACTIONS[action](api, stranger["token"], listing_of("ACTIVE"))

    assert response.status_code == 404
    assert problem_code(response) == "listing-not-found"


@pytest.mark.parametrize("action", ["edit", "submit"])
def test_an_admin_may_not_edit_or_submit_someone_elses_listing(
    api: TestClient, admin: dict[str, str], listing_of: MakeListing, action: str
) -> None:
    response = SELLER_ACTIONS[action](api, admin["token"], listing_of())

    assert response.status_code == 403
    assert problem_code(response) == "seller-only"


def test_an_admin_who_is_the_seller_acts_by_the_sellers_rules(
    api: TestClient, db_session: Session, admin: dict[str, str], published_event: Event
) -> None:
    ticket = make_verified_ticket(
        db_session,
        event_id=published_event.id,
        original_owner_id=admin["id"],
        current_owner_id=admin["id"],
    )
    listing = make_listing(db_session, ticket=ticket, asking_price_paise=200_000)

    assert patch(api, admin["token"], listing, {"asking_price_paise": 190_000}).status_code == 200
    assert submit(api, admin["token"], listing).status_code == 200
    # An admin acting as an admin cannot cancel a pending listing; the seller can.
    assert cancel(api, admin["token"], listing).status_code == 200

    rows = actions(db_session, listing)
    assert [row.action for row in rows] == [
        "listing.price_changed",
        "listing.submitted",
        "listing.cancelled",
    ]
    assert rows[-1].details["by"] == "seller"
