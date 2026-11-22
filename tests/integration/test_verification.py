"""Admins review pending tickets and mark each VERIFIED or REJECTED, exactly once."""

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.modules.events.models import Event
from app.modules.tickets.models import Ticket
from tests.integration.api import bearer
from tests.integration.factories import make_ticket

pytestmark = pytest.mark.integration

PENDING = "/api/v1/admin/tickets/pending"


def verify(api: TestClient, account: dict[str, str], ticket: Ticket, **body: Any) -> Any:
    return api.post(
        f"/api/v1/admin/tickets/{ticket.id}/verify", json=body, headers=bearer(account["token"])
    )


def problem_code(response: Any) -> str:
    return response.json()["type"].rsplit(":", 1)[-1]


def test_the_queue_lists_pending_tickets_oldest_first(
    api: TestClient, admin: dict[str, str], db_session: Session, published_event: Event
) -> None:
    older = make_ticket(db_session, event_id=published_event.id)
    newer = make_ticket(db_session, event_id=published_event.id)
    decided = make_ticket(db_session, event_id=published_event.id, verification_status="VERIFIED")
    # One transaction means one now(); give the rows distinct ages.
    db_session.execute(
        update(Ticket)
        .where(Ticket.id == older.id)
        .values(created_at=Ticket.created_at - timedelta(minutes=5))
    )

    items = api.get(PENDING, params={"limit": 100}, headers=bearer(admin["token"])).json()["items"]
    ids = [item["id"] for item in items]

    assert ids.index(str(older.id)) < ids.index(str(newer.id))
    assert str(decided.id) not in ids


def test_a_user_cannot_see_the_queue(api: TestClient, user: dict[str, str]) -> None:
    assert api.get(PENDING, headers=bearer(user["token"])).status_code == 403


def test_verifying_sets_who_and_when(
    api: TestClient, admin: dict[str, str], owned_ticket: Ticket, db_session: Session
) -> None:
    response = verify(api, admin, owned_ticket, decision="VERIFIED", transfer_eligible=True)

    assert response.status_code == 200, response.text
    assert response.json()["verification_status"] == "VERIFIED"
    assert response.json()["transfer_eligible"] is True
    db_session.expire_all()
    row = db_session.get(Ticket, owned_ticket.id)
    assert str(row.verified_by) == admin["id"]
    assert row.verified_at is not None


def test_verifying_without_transfer_eligibility_keeps_it_false(
    api: TestClient, admin: dict[str, str], owned_ticket: Ticket
) -> None:
    response = verify(api, admin, owned_ticket, decision="VERIFIED")

    assert response.json()["transfer_eligible"] is False


def test_rejecting_needs_a_reason(
    api: TestClient, admin: dict[str, str], owned_ticket: Ticket
) -> None:
    assert verify(api, admin, owned_ticket, decision="REJECTED").status_code == 422
    assert verify(api, admin, owned_ticket, decision="REJECTED", note="   ").status_code == 422


def test_rejecting_forces_transfer_eligible_false_and_the_owner_sees_why(
    api: TestClient, admin: dict[str, str], user: dict[str, str], owned_ticket: Ticket
) -> None:
    response = verify(
        api,
        admin,
        owned_ticket,
        decision="REJECTED",
        transfer_eligible=True,
        note="The order number does not match the provider's records.",
    )

    assert response.status_code == 200
    assert response.json()["transfer_eligible"] is False
    seen = api.get(f"/api/v1/tickets/{owned_ticket.id}", headers=bearer(user["token"])).json()
    assert seen["verification_status"] == "REJECTED"
    assert seen["verification_note"] == "The order number does not match the provider's records."


def test_a_note_over_500_characters_is_422(
    api: TestClient, admin: dict[str, str], owned_ticket: Ticket
) -> None:
    response = verify(api, admin, owned_ticket, decision="REJECTED", note="x" * 501)

    assert response.status_code == 422


def test_an_unknown_decision_is_422(
    api: TestClient, admin: dict[str, str], owned_ticket: Ticket
) -> None:
    assert verify(api, admin, owned_ticket, decision="PENDING").status_code == 422


def test_a_second_decision_is_409(
    api: TestClient, admin: dict[str, str], owned_ticket: Ticket
) -> None:
    assert verify(api, admin, owned_ticket, decision="VERIFIED").status_code == 200

    again = verify(api, admin, owned_ticket, decision="REJECTED", note="Changed my mind.")

    assert again.status_code == 409
    assert problem_code(again) == "ticket-not-pending"


def test_an_unknown_ticket_is_404(api: TestClient, admin: dict[str, str]) -> None:
    response = verify(
        api, admin, Ticket(id="00000000-0000-4000-8000-000000000000"), decision="VERIFIED"
    )

    assert response.status_code == 404
    assert problem_code(response) == "ticket-not-found"


def test_a_user_cannot_verify(api: TestClient, user: dict[str, str], owned_ticket: Ticket) -> None:
    response = verify(api, user, owned_ticket, decision="VERIFIED")

    assert response.status_code == 403
    assert problem_code(response) == "admin-required"
