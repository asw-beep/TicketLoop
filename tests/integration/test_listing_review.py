"""Admin review: approve or reject a listing waiting for review, and the two admin lists.

Approve and reject act only from PENDING_VERIFICATION, whatever `ALLOWED` permits. Approve
checks the ticket, the event and the expiry again; a listing that fails is rejected instead.
No admin reviews their own listing.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.events.models import Event
from app.modules.identity.models import UserRole
from app.modules.listings.models import Listing
from app.modules.tickets.models import Ticket
from scripts.make_admin import set_role
from tests.integration.api import bearer, problem_code
from tests.integration.conftest import new_account
from tests.integration.factories import make_listing, make_verified_ticket

pytestmark = pytest.mark.integration

ADMIN_LISTINGS = "/api/v1/admin/listings"
UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"


def approve(api: TestClient, token: str, listing_id: Any) -> Any:
    return api.post(f"{ADMIN_LISTINGS}/{listing_id}/approve", headers=bearer(token))


def reject(api: TestClient, token: str, listing_id: Any, body: Any = None) -> Any:
    body = {"reason": "Seat details missing."} if body is None else body
    return api.post(f"{ADMIN_LISTINGS}/{listing_id}/reject", json=body, headers=bearer(token))


def audit_actions(db: Session, listing: Listing) -> list[str]:
    return list(
        db.scalars(
            select(AuditLog.action)
            .where(AuditLog.entity_type == "listing", AuditLog.entity_id == listing.id)
            .order_by(AuditLog.created_at, AuditLog.id)
        )
    )


def still(db: Session, listing: Listing) -> str:
    db.expire_all()
    return db.get(Listing, listing.id).status


@pytest.fixture
def pending(db_session: Session, verified_ticket: Ticket) -> Listing:
    """`user`'s listing, waiting for review."""
    return make_listing(db_session, ticket=verified_ticket, status="PENDING_VERIFICATION")


# ---------------------------------------------------------------- approve and reject


def test_approve_makes_the_listing_active_and_records_the_reviewer(
    api: TestClient, db_session: Session, admin: dict[str, str], pending: Listing
) -> None:
    response = approve(api, admin["token"], pending.id)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ACTIVE"
    assert response.json()["reviewed_at"] is not None
    db_session.expire_all()
    assert str(db_session.get(Listing, pending.id).reviewed_by) == admin["id"]
    (row,) = db_session.scalars(select(AuditLog).where(AuditLog.entity_id == pending.id))
    assert row.action == "listing.approved"
    assert str(row.actor_id) == admin["id"]


def test_reject_records_the_trimmed_reason(
    api: TestClient, db_session: Session, admin: dict[str, str], pending: Listing
) -> None:
    response = reject(api, admin["token"], pending.id, {"reason": "  Seat details missing.  "})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "REJECTED"
    assert response.json()["rejection_reason"] == "Seat details missing."
    (row,) = db_session.scalars(select(AuditLog).where(AuditLog.entity_id == pending.id))
    assert row.action == "listing.rejected"
    assert row.details["reason"] == "Seat details missing."


@pytest.mark.parametrize(
    "body",
    [{}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * 501}],
    ids=["missing", "empty", "whitespace", "too long"],
)
def test_a_rejection_needs_a_real_reason(
    api: TestClient,
    db_session: Session,
    admin: dict[str, str],
    pending: Listing,
    body: dict[str, str],
) -> None:
    response = reject(api, admin["token"], pending.id, body)

    assert response.status_code == 422
    assert still(db_session, pending) == "PENDING_VERIFICATION"


# ---------------------------------------------------------------- from-states


@pytest.mark.parametrize("status", ["DRAFT", "ACTIVE", "RESERVED"])
@pytest.mark.parametrize("act", [approve, reject], ids=["approve", "reject"])
def test_only_a_pending_listing_can_be_reviewed(
    api: TestClient,
    db_session: Session,
    admin: dict[str, str],
    verified_ticket: Ticket,
    status: str,
    act: Any,
) -> None:
    listing = make_listing(db_session, ticket=verified_ticket, status=status)

    response = act(api, admin["token"], listing.id)

    assert response.status_code == 409
    assert problem_code(response) == "listing-not-pending"
    # RESERVED -> ACTIVE is in ALLOWED (a released reservation), but never through approve.
    assert still(db_session, listing) == status


# ---------------------------------------------------------------- approve checks again


def expire(db: Session, listing: Listing, *_: Any) -> None:
    listing.expires_at = datetime.now(UTC) - timedelta(minutes=1)


def start_event(db: Session, listing: Listing, event: Event, ticket: Ticket) -> None:
    event.starts_at = datetime.now(UTC) - timedelta(hours=1)


def void_ticket(db: Session, listing: Listing, event: Event, ticket: Ticket) -> None:
    ticket.status = "VOID"


@pytest.mark.parametrize(
    "change", [expire, start_event, void_ticket], ids=["expired", "event started", "void"]
)
def test_approve_refuses_a_listing_that_is_no_longer_listable(
    api: TestClient,
    db_session: Session,
    admin: dict[str, str],
    pending: Listing,
    published_event: Event,
    verified_ticket: Ticket,
    change: Any,
) -> None:
    change(db_session, pending, published_event, verified_ticket)
    db_session.flush()

    response = approve(api, admin["token"], pending.id)

    assert response.status_code == 409
    assert problem_code(response) == "listing-not-approvable"
    assert still(db_session, pending) == "PENDING_VERIFICATION"


# ---------------------------------------------------------------- no self-review


@pytest.mark.parametrize("act", [approve, reject], ids=["approve", "reject"])
def test_an_admin_cannot_review_their_own_listing(
    api: TestClient,
    db_session: Session,
    admin: dict[str, str],
    published_event: Event,
    act: Any,
) -> None:
    ticket = make_verified_ticket(
        db_session,
        event_id=published_event.id,
        original_owner_id=admin["id"],
        current_owner_id=admin["id"],
    )
    own = make_listing(db_session, ticket=ticket, status="PENDING_VERIFICATION")

    response = act(api, admin["token"], own.id)

    assert response.status_code == 403
    assert problem_code(response) == "own-listing-review"
    assert still(db_session, own) == "PENDING_VERIFICATION"
    assert audit_actions(db_session, own) == []

    second_admin = new_account(api)
    set_role(db_session, second_admin["email"], UserRole.ADMIN)
    db_session.flush()
    assert approve(api, second_admin["token"], own.id).status_code == 200


# ---------------------------------------------------------------- lists


def test_the_pending_queue_is_oldest_first_and_only_pending(
    api: TestClient, db_session: Session, admin: dict[str, str], published_event: Event
) -> None:
    now = datetime.now(UTC)

    def listing(status: str, minutes_ago: int) -> Listing:
        ticket = make_verified_ticket(db_session, event_id=published_event.id)
        created = now - timedelta(minutes=minutes_ago)
        return make_listing(db_session, ticket=ticket, status=status, created_at=created)

    newer = listing("PENDING_VERIFICATION", 1)
    older = listing("PENDING_VERIFICATION", 5)
    listing("ACTIVE", 10)
    listing("DRAFT", 10)

    response = api.get(f"{ADMIN_LISTINGS}/pending", headers=bearer(admin["token"]))

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [str(older.id), str(newer.id)]


def test_all_listings_filter_by_status_and_ticket(
    api: TestClient,
    db_session: Session,
    admin: dict[str, str],
    verified_ticket: Ticket,
    published_event: Event,
) -> None:
    now = datetime.now(UTC)
    cancelled = make_listing(
        db_session,
        ticket=verified_ticket,
        status="CANCELLED",
        created_at=now - timedelta(minutes=2),
    )
    active = make_listing(
        db_session,
        ticket=verified_ticket,
        status="ACTIVE",
        created_at=now - timedelta(minutes=1),
    )
    other = make_listing(
        db_session,
        ticket=make_verified_ticket(db_session, event_id=published_event.id),
        status="ACTIVE",
    )

    def ids(query: str) -> list[str]:
        response = api.get(f"{ADMIN_LISTINGS}{query}", headers=bearer(admin["token"]))
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["items"]]

    assert ids(f"?ticket_id={verified_ticket.id}") == [str(active.id), str(cancelled.id)]
    assert str(cancelled.id) in ids("?status=CANCELLED")
    assert str(other.id) not in ids("?status=CANCELLED")
    assert str(other.id) in ids("?status=ACTIVE")


@pytest.mark.parametrize("query", ["?status=BOGUS", "?ticket_id=not-a-uuid"])
def test_bad_filters_on_all_listings_are_422(
    api: TestClient, admin: dict[str, str], query: str
) -> None:
    response = api.get(f"{ADMIN_LISTINGS}{query}", headers=bearer(admin["token"]))

    assert response.status_code == 422


def test_approving_an_unknown_listing_is_404(api: TestClient, admin: dict[str, str]) -> None:
    response = approve(api, admin["token"], UNKNOWN_ID)

    assert response.status_code == 404
    assert problem_code(response) == "listing-not-found"
