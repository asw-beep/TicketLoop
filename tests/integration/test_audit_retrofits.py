"""The admin actions M4 built now write audit rows: ticket verification, events, providers.

And no admin verifies a ticket they own, the same rule as for reviewing their own listing.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.events.models import Event
from app.modules.identity.models import UserRole
from app.modules.tickets.models import Ticket
from scripts.make_admin import set_role
from tests.integration.api import bearer, problem_code
from tests.integration.conftest import new_account
from tests.integration.factories import make_event, make_provider, make_ticket

pytestmark = pytest.mark.integration

ADMIN = "/api/v1/admin"


def audit_rows(db: Session, entity_id: Any) -> list[AuditLog]:
    return list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.entity_id == entity_id)
            .order_by(AuditLog.created_at, AuditLog.id)
        )
    )


def verify(api: TestClient, token: str, ticket: Ticket, body: dict[str, Any]) -> Any:
    return api.post(f"{ADMIN}/tickets/{ticket.id}/verify", json=body, headers=bearer(token))


def edit_event(api: TestClient, token: str, event: Event, body: dict[str, Any]) -> Any:
    return api.patch(f"{ADMIN}/events/{event.id}", json=body, headers=bearer(token))


# ---------------------------------------------------------------- ticket verification


def test_verifying_a_ticket_is_audited(
    api: TestClient, db_session: Session, admin: dict[str, str], owned_ticket: Ticket
) -> None:
    response = verify(
        api, admin["token"], owned_ticket, {"decision": "VERIFIED", "transfer_eligible": True}
    )

    assert response.status_code == 200, response.text
    (row,) = audit_rows(db_session, owned_ticket.id)
    assert (row.entity_type, row.action) == ("ticket", "ticket.verified")
    assert str(row.actor_id) == admin["id"]
    assert row.details == {"transfer_eligible": True, "note": None}


def test_rejecting_a_ticket_is_audited_with_the_note(
    api: TestClient, db_session: Session, admin: dict[str, str], owned_ticket: Ticket
) -> None:
    verify(api, admin["token"], owned_ticket, {"decision": "REJECTED", "note": "Blurry scan."})

    (row,) = audit_rows(db_session, owned_ticket.id)
    assert row.action == "ticket.rejected"
    assert row.details == {"transfer_eligible": False, "note": "Blurry scan."}


def test_an_admin_cannot_verify_a_ticket_they_own(
    api: TestClient, db_session: Session, admin: dict[str, str], published_event: Event
) -> None:
    own = make_ticket(
        db_session,
        event_id=published_event.id,
        original_owner_id=admin["id"],
        current_owner_id=admin["id"],
    )
    body = {"decision": "VERIFIED", "transfer_eligible": True}

    response = verify(api, admin["token"], own, body)

    assert response.status_code == 403
    assert problem_code(response) == "own-ticket-review"
    db_session.refresh(own)
    assert own.verification_status == "PENDING"
    assert audit_rows(db_session, own.id) == []

    second_admin = new_account(api)
    set_role(db_session, second_admin["email"], UserRole.ADMIN)
    db_session.flush()
    assert verify(api, second_admin["token"], own, body).status_code == 200


# ---------------------------------------------------------------- events


def test_creating_an_event_is_audited(
    api: TestClient, db_session: Session, admin: dict[str, str]
) -> None:
    starts_at = datetime.now(UTC).replace(microsecond=0) + timedelta(days=20)
    body = {
        "name": "Monsoon Jazz",
        "category": "CONCERT",
        "venue": "Bolgatty Palace",
        "city": "Kochi",
        "starts_at": starts_at.isoformat(),
        "organizer": "Kochi Arts",
        "provider_id": str(make_provider(db_session).id),
    }

    response = api.post(f"{ADMIN}/events", json=body, headers=bearer(admin["token"]))

    assert response.status_code == 201, response.text
    (row,) = audit_rows(db_session, uuid.UUID(response.json()["id"]))
    assert (row.entity_type, row.action) == ("event", "event.created")
    assert str(row.actor_id) == admin["id"]
    assert row.details["name"] == "Monsoon Jazz"
    assert datetime.fromisoformat(row.details["starts_at"]) == starts_at


def test_a_field_edit_records_the_changed_fields(
    api: TestClient, db_session: Session, admin: dict[str, str]
) -> None:
    event = make_event(db_session)

    edit_event(api, admin["token"], event, {"venue": "Durbar Hall", "name": "Renamed"})

    (row,) = audit_rows(db_session, event.id)
    assert row.action == "event.updated"
    assert row.details == {"changed": ["name", "venue"]}


def test_an_edit_that_changes_nothing_writes_no_row(
    api: TestClient, db_session: Session, admin: dict[str, str]
) -> None:
    event = make_event(db_session)

    response = edit_event(api, admin["token"], event, {"name": event.name})

    assert response.status_code == 200
    assert audit_rows(db_session, event.id) == []


def test_publishing_is_audited_with_from_and_to(
    api: TestClient, db_session: Session, admin: dict[str, str]
) -> None:
    event = make_event(db_session)

    edit_event(api, admin["token"], event, {"status": "PUBLISHED"})

    (row,) = audit_rows(db_session, event.id)
    assert row.action == "event.published"
    assert row.details == {"from": "DRAFT", "to": "PUBLISHED"}


def test_an_edit_that_also_publishes_writes_one_row(
    api: TestClient, db_session: Session, admin: dict[str, str]
) -> None:
    event = make_event(db_session)

    edit_event(api, admin["token"], event, {"name": "Renamed", "status": "PUBLISHED"})

    assert [row.action for row in audit_rows(db_session, event.id)] == ["event.published"]


def test_cancelling_is_audited(
    api: TestClient, db_session: Session, admin: dict[str, str], published_event: Event
) -> None:
    edit_event(api, admin["token"], published_event, {"status": "CANCELLED"})

    (row,) = audit_rows(db_session, published_event.id)
    assert row.action == "event.cancelled"
    assert (row.details["from"], row.details["to"]) == ("PUBLISHED", "CANCELLED")


def test_a_refused_change_writes_no_row(
    api: TestClient, db_session: Session, admin: dict[str, str]
) -> None:
    event = make_event(db_session)  # DRAFT cannot go straight to COMPLETED

    response = edit_event(api, admin["token"], event, {"status": "COMPLETED"})

    assert response.status_code == 409
    assert audit_rows(db_session, event.id) == []


# ---------------------------------------------------------------- providers


def test_creating_a_provider_is_audited(
    api: TestClient, db_session: Session, admin: dict[str, str]
) -> None:
    code = f"provider-{uuid.uuid4().hex[:8]}"

    response = api.post(
        f"{ADMIN}/providers",
        json={"code": code, "name": "Box Office"},
        headers=bearer(admin["token"]),
    )

    assert response.status_code == 201, response.text
    (row,) = audit_rows(db_session, uuid.UUID(response.json()["id"]))
    assert (row.entity_type, row.action) == ("provider", "provider.created")
    assert row.details == {"code": code}
