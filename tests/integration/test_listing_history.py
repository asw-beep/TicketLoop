"""GET /listings/{id}/history: the listing's audit rows, oldest first, for its seller and admins.

Who acted is shown only as a role (seller, admin or system). The admin's identity never
appears, and each entry's details keep only the keys that are safe to show the seller.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.modules.audit.service import record
from app.modules.listings.models import Listing, ListingStatus
from app.modules.listings.state import transition
from app.modules.tickets.models import Ticket
from tests.integration.api import bearer, problem_code
from tests.integration.conftest import new_account
from tests.integration.factories import make_listing

pytestmark = pytest.mark.integration

LISTINGS = "/api/v1/listings"
ENTRY_KEYS = {"action", "from_status", "to_status", "actor_role", "details", "created_at"}


def history(api: TestClient, token: str, listing_id: Any) -> Any:
    return api.get(f"{LISTINGS}/{listing_id}/history", headers=bearer(token))


def told_and_rejected(
    api: TestClient, db: Session, user: dict[str, str], admin: dict[str, str], ticket: Ticket
) -> str:
    """Create, cut the price, submit, then an admin rejects it. Returns the listing ID."""
    token = bearer(user["token"])
    body = {"ticket_id": str(ticket.id), "asking_price_paise": 200_000}
    listing_id = api.post(LISTINGS, json=body, headers=token).json()["id"]
    patched = api.patch(
        f"{LISTINGS}/{listing_id}", json={"asking_price_paise": 180_000}, headers=token
    )
    assert patched.status_code == 200
    assert api.post(f"{LISTINGS}/{listing_id}/submit", headers=token).status_code == 200
    listing = db.get(Listing, listing_id)
    db.refresh(listing)
    transition(
        db,
        listing,
        ListingStatus.REJECTED,
        actor_id=admin["id"],
        values={
            "rejection_reason": "Seat details missing.",
            "reviewed_by": admin["id"],
            "reviewed_at": func.now(),
        },
        details={"reason": "Seat details missing."},
    )
    db.flush()
    return listing_id


def test_history_tells_the_story_in_order(
    api: TestClient,
    db_session: Session,
    user: dict[str, str],
    admin: dict[str, str],
    verified_ticket: Ticket,
) -> None:
    listing_id = told_and_rejected(api, db_session, user, admin, verified_ticket)

    response = history(api, user["token"], listing_id)

    assert response.status_code == 200, response.text
    entries = response.json()["items"]
    assert [e["action"] for e in entries] == [
        "listing.created",
        "listing.price_changed",
        "listing.submitted",
        "listing.rejected",
    ]
    assert [e["actor_role"] for e in entries] == ["seller", "seller", "seller", "admin"]
    assert [e["to_status"] for e in entries] == [
        "DRAFT",
        None,
        "PENDING_VERIFICATION",
        "REJECTED",
    ]
    assert entries[1]["details"] == {"old_price_paise": 200_000, "new_price_paise": 180_000}
    assert entries[3]["from_status"] == "PENDING_VERIFICATION"
    assert entries[3]["details"] == {"reason": "Seat details missing."}


def test_a_system_change_reads_as_system(
    api: TestClient, db_session: Session, user: dict[str, str], verified_ticket: Ticket
) -> None:
    listing = make_listing(db_session, ticket=verified_ticket, status="ACTIVE")
    transition(db_session, listing, ListingStatus.EXPIRED, actor_id=None)

    (entry,) = history(api, user["token"], listing.id).json()["items"]

    assert entry["actor_role"] == "system"
    assert entry["to_status"] == "EXPIRED"


def test_details_keep_only_safe_keys(
    api: TestClient, db_session: Session, user: dict[str, str], verified_ticket: Ticket
) -> None:
    listing = make_listing(db_session, ticket=verified_ticket, status="CANCELLED")
    record(
        db_session,
        actor_id=None,
        entity_type="listing",
        entity_id=listing.id,
        action="listing.cancelled",
        details={
            "from": "ACTIVE",
            "to": "CANCELLED",
            "reason": "event_cancelled",
            "email": "x@example.in",
        },
    )

    (entry,) = history(api, user["token"], listing.id).json()["items"]

    assert entry["details"] == {"reason": "event_cancelled"}
    assert (entry["from_status"], entry["to_status"]) == ("ACTIVE", "CANCELLED")


@pytest.mark.parametrize("status", ["DRAFT", "ACTIVE"])
def test_strangers_get_404_and_admins_can_read(
    api: TestClient,
    db_session: Session,
    admin: dict[str, str],
    verified_ticket: Ticket,
    status: str,
) -> None:
    listing = make_listing(db_session, ticket=verified_ticket, status=status)
    stranger = new_account(api)

    refused = history(api, stranger["token"], listing.id)
    assert refused.status_code == 404
    assert problem_code(refused) == "listing-not-found"
    assert history(api, admin["token"], listing.id).status_code == 200


def test_no_entry_names_the_admin(
    api: TestClient,
    db_session: Session,
    user: dict[str, str],
    admin: dict[str, str],
    verified_ticket: Ticket,
) -> None:
    listing_id = told_and_rejected(api, db_session, user, admin, verified_ticket)

    response = history(api, user["token"], listing_id)

    assert all(set(entry) == ENTRY_KEYS for entry in response.json()["items"])
    assert admin["id"] not in response.text
    assert admin["email"] not in response.text
