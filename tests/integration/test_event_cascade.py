"""What an event change does to its listings.

Cancelling an event cancels its open listings in the same transaction, one audit row each: an
event that is not happening should not stay on sale. Moving its start earlier pulls back any
listing expiry later than the new start: a listing never outlives its event's start.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.events.models import Event
from app.modules.listings.models import Listing, ListingStatus
from tests.integration.api import bearer
from tests.integration.factories import make_event, make_listing, make_verified_ticket

pytestmark = pytest.mark.integration

OPEN_BEFORE_RESERVATION = {"DRAFT", "PENDING_VERIFICATION", "ACTIVE"}


def edit_event(api: TestClient, token: str, event: Event, body: dict[str, Any]) -> Any:
    return api.patch(f"/api/v1/admin/events/{event.id}", json=body, headers=bearer(token))


def one_listing_per_status(db: Session, event: Event) -> dict[str, Listing]:
    return {
        status: make_listing(db, ticket=make_verified_ticket(db, event_id=event.id), status=status)
        for status in ListingStatus
    }


def audit_rows(db: Session, entity_id: Any, action: str) -> list[AuditLog]:
    return list(
        db.scalars(
            select(AuditLog).where(AuditLog.entity_id == entity_id, AuditLog.action == action)
        )
    )


def test_cancelling_an_event_cancels_its_open_listings(
    api: TestClient, db_session: Session, admin: dict[str, str], published_event: Event
) -> None:
    listings = one_listing_per_status(db_session, published_event)

    response = edit_event(api, admin["token"], published_event, {"status": "CANCELLED"})

    assert response.status_code == 200, response.text
    db_session.expire_all()
    for status, listing in listings.items():
        if status in OPEN_BEFORE_RESERVATION:
            assert listing.status == "CANCELLED"
            (row,) = audit_rows(db_session, listing.id, "listing.cancelled")
            assert str(row.actor_id) == admin["id"]
            assert row.details["reason"] == "event_cancelled"
            assert row.details["from"] == status
        else:
            assert listing.status == status
            assert audit_rows(db_session, listing.id, "listing.cancelled") == []
    (event_row,) = audit_rows(db_session, published_event.id, "event.cancelled")
    assert event_row.details["cancelled_listings"] == 3


def test_another_events_listings_are_untouched(
    api: TestClient, db_session: Session, admin: dict[str, str], published_event: Event
) -> None:
    other = make_event(db_session, status="PUBLISHED")
    listing = make_listing(
        db_session, ticket=make_verified_ticket(db_session, event_id=other.id), status="ACTIVE"
    )

    edit_event(api, admin["token"], published_event, {"status": "CANCELLED"})

    db_session.expire_all()
    assert listing.status == "ACTIVE"


def test_moving_the_start_earlier_pulls_back_later_expiries(
    api: TestClient, db_session: Session, admin: dict[str, str], published_event: Event
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    late = make_listing(
        db_session,
        ticket=make_verified_ticket(db_session, event_id=published_event.id),
        status="ACTIVE",
        expires_at=now + timedelta(days=29),
    )
    early_expiry = now + timedelta(days=10)
    early = make_listing(
        db_session,
        ticket=make_verified_ticket(db_session, event_id=published_event.id),
        status="DRAFT",
        expires_at=early_expiry,
    )
    new_start = now + timedelta(days=20)

    response = edit_event(
        api, admin["token"], published_event, {"starts_at": new_start.isoformat()}
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert late.expires_at == new_start
    assert early.expires_at == early_expiry
    (event_row,) = audit_rows(db_session, published_event.id, "event.updated")
    assert event_row.details["listings_expiry_moved"] == 1


def test_postponing_an_event_carries_listings_that_end_at_its_start_forward(
    api: TestClient, db_session: Session, admin: dict[str, str], published_event: Event
) -> None:
    """A listing created without an end runs until the event starts. If the event is put back,
    it should still run until the event starts, not end at the old time."""
    old_start = published_event.starts_at

    def listing(status: str, expires_at: datetime) -> Listing:
        ticket = make_verified_ticket(db_session, event_id=published_event.id)
        return make_listing(db_session, ticket=ticket, status=status, expires_at=expires_at)

    active = listing("ACTIVE", old_start)
    pending = listing("PENDING_VERIFICATION", old_start)
    earlier_end = old_start - timedelta(days=3)
    ends_earlier = listing("ACTIVE", earlier_end)
    closed = listing("EXPIRED", old_start)
    new_start = old_start + timedelta(days=10)

    response = edit_event(
        api, admin["token"], published_event, {"starts_at": new_start.isoformat()}
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert active.expires_at == new_start
    assert pending.expires_at == new_start
    assert ends_earlier.expires_at == earlier_end
    assert closed.expires_at == old_start
    (event_row,) = audit_rows(db_session, published_event.id, "event.updated")
    assert event_row.details["listings_expiry_moved"] == 2
