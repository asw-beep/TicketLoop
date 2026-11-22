"""Tickets (FR-03): any user registers a ticket they own; only they and admins can read it."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.modules.events.models import Event
from app.modules.tickets.models import Ticket
from tests.integration.api import bearer
from tests.integration.conftest import new_account
from tests.integration.factories import make_event, make_ticket

pytestmark = pytest.mark.integration

TICKETS = "/api/v1/tickets"
UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"


def ticket_body(event: Event, **fields: Any) -> dict[str, Any]:
    body = {
        "event_id": str(event.id),
        "ticket_category": "GENERAL",
        "seat_details": "Block C, Row 4, Seat 12",
        "original_price_paise": 250_000,
        "purchase_reference": "BMS-ORDER-77812",
        "provider_reference": "FP-TKT-0001",
    }
    return body | fields


def register_ticket(api: TestClient, owner: dict[str, str], event: Event, **fields: Any) -> Any:
    return api.post(TICKETS, json=ticket_body(event, **fields), headers=bearer(owner["token"]))


def problem_code(response: Any) -> str:
    return response.json()["type"].rsplit(":", 1)[-1]


def test_registering_takes_the_owner_from_the_token(
    api: TestClient, user: dict[str, str], published_event: Event, db_session: Session
) -> None:
    response = register_ticket(api, user, published_event)

    assert response.status_code == 201, response.text
    ticket = response.json()
    assert ticket["verification_status"] == "PENDING"
    assert ticket["transfer_eligible"] is False
    assert ticket["status"] == "ACTIVE"
    assert ticket["event"]["name"] == published_event.name
    assert ticket["purchase_reference"] == "BMS-ORDER-77812"
    row = db_session.get(Ticket, ticket["id"])
    assert str(row.current_owner_id) == user["id"]
    assert str(row.original_owner_id) == user["id"]
    mine = api.get(f"{TICKETS}/me", headers=bearer(user["token"])).json()["items"]
    assert [t["id"] for t in mine] == [ticket["id"]]


def test_a_body_that_names_an_owner_is_422(
    api: TestClient, user: dict[str, str], published_event: Event
) -> None:
    response = register_ticket(api, user, published_event, current_owner_id=user["id"])

    assert response.status_code == 422


def test_a_guest_cannot_register_a_ticket(api: TestClient, published_event: Event) -> None:
    assert api.post(TICKETS, json=ticket_body(published_event)).status_code == 401


def test_the_same_provider_reference_twice_is_409(
    api: TestClient, user: dict[str, str], published_event: Event
) -> None:
    assert register_ticket(api, user, published_event).status_code == 201

    again = register_ticket(api, new_account(api), published_event)

    assert again.status_code == 409
    assert problem_code(again) == "ticket-already-registered"


def test_a_draft_event_is_404(api: TestClient, user: dict[str, str], db_session: Session) -> None:
    response = register_ticket(api, user, make_event(db_session))

    assert response.status_code == 404
    assert problem_code(response) == "event-not-found"


def test_an_unknown_event_is_404(api: TestClient, user: dict[str, str]) -> None:
    body = ticket_body(Event(id=UNKNOWN_ID))
    response = api.post(TICKETS, json=body, headers=bearer(user["token"]))

    assert response.status_code == 404


@pytest.mark.parametrize("status", ["CANCELLED", "COMPLETED"])
def test_a_closed_event_is_409(
    api: TestClient, user: dict[str, str], db_session: Session, status: str
) -> None:
    response = register_ticket(api, user, make_event(db_session, status=status))

    assert response.status_code == 409
    assert problem_code(response) == "event-not-open"


def test_an_event_that_has_started_is_422(
    api: TestClient, user: dict[str, str], db_session: Session
) -> None:
    started = make_event(
        db_session, status="PUBLISHED", starts_at=datetime.now(UTC) - timedelta(minutes=5)
    )

    response = register_ticket(api, user, started)

    assert response.status_code == 422
    assert problem_code(response) == "event-started"


@pytest.mark.parametrize("field", ["provider_reference", "purchase_reference", "ticket_category"])
def test_a_whitespace_only_text_field_is_422(
    api: TestClient, user: dict[str, str], published_event: Event, field: str
) -> None:
    response = register_ticket(api, user, published_event, **{field: "   "})

    assert response.status_code == 422


def test_text_fields_are_trimmed(
    api: TestClient, user: dict[str, str], published_event: Event
) -> None:
    response = register_ticket(api, user, published_event, provider_reference="  FP-9  ")

    assert response.json()["provider_reference"] == "FP-9"


@pytest.mark.parametrize("price", [0, -100, 10_000_001])
def test_prices_outside_1_paise_to_1_lakh_rupees_are_422(
    api: TestClient, user: dict[str, str], published_event: Event, price: int
) -> None:
    response = register_ticket(api, user, published_event, original_price_paise=price)

    assert response.status_code == 422


def test_my_tickets_lists_only_mine_newest_first(
    api: TestClient, user: dict[str, str], published_event: Event, db_session: Session
) -> None:
    first = register_ticket(api, user, published_event, provider_reference="A").json()
    second = register_ticket(api, user, published_event, provider_reference="B").json()
    make_ticket(db_session, event_id=published_event.id)  # someone else's
    # now() is the transaction's start time, and a test is one transaction, so both rows got
    # the same created_at. Real requests are separate transactions; age the first one here.
    db_session.execute(
        update(Ticket)
        .where(Ticket.id == first["id"])
        .values(created_at=Ticket.created_at - timedelta(minutes=1))
    )

    mine = api.get(f"{TICKETS}/me", headers=bearer(user["token"])).json()

    assert [t["id"] for t in mine["items"]] == [second["id"], first["id"]]
    assert mine["next_cursor"] is None


def test_the_owner_and_an_admin_can_read_a_ticket(
    api: TestClient, user: dict[str, str], admin: dict[str, str], published_event: Event
) -> None:
    ticket = register_ticket(api, user, published_event).json()

    as_owner = api.get(f"{TICKETS}/{ticket['id']}", headers=bearer(user["token"]))
    as_admin = api.get(f"{TICKETS}/{ticket['id']}", headers=bearer(admin["token"]))

    assert as_owner.status_code == 200
    assert as_admin.status_code == 200
    assert as_admin.json()["provider_reference"] == "FP-TKT-0001"


def test_an_unknown_ticket_id_is_404(api: TestClient, user: dict[str, str]) -> None:
    response = api.get(f"{TICKETS}/{UNKNOWN_ID}", headers=bearer(user["token"]))

    assert response.status_code == 404
    assert problem_code(response) == "ticket-not-found"
