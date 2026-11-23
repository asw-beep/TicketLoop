"""POST /listings: an owner offers a verified ticket at no more than face value, as a DRAFT.

Each guard has its own test and its own error code, in the order the service checks them.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.events.models import Event
from app.modules.tickets.models import Ticket
from tests.integration.api import bearer, problem_code
from tests.integration.conftest import new_account
from tests.integration.factories import make_event, make_listing, make_verified_ticket

pytestmark = pytest.mark.integration

LISTINGS = "/api/v1/listings"


def create(api: TestClient, token: str, ticket_id: Any, **fields: Any) -> Any:
    body = {"ticket_id": str(ticket_id), "asking_price_paise": 200_000} | fields
    return api.post(LISTINGS, json=body, headers=bearer(token))


def users_ticket(db: Session, user: dict[str, str], event: Event, **fields: Any) -> Ticket:
    return make_verified_ticket(
        db,
        event_id=event.id,
        original_owner_id=user["id"],
        current_owner_id=user["id"],
        **fields,
    )


def test_creates_a_draft_copying_event_and_price_from_the_ticket(
    api: TestClient, user: dict[str, str], verified_ticket: Ticket, published_event: Event
) -> None:
    response = create(api, user["token"], verified_ticket.id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "DRAFT"
    assert body["seller_id"] == user["id"]
    assert body["asking_price_paise"] == 200_000
    assert body["original_price_paise"] == 250_000
    assert body["event"]["id"] == str(verified_ticket.event_id)
    assert datetime.fromisoformat(body["expires_at"]) == published_event.starts_at


def test_writes_listing_created_with_the_request_id(
    api: TestClient, db_session: Session, user: dict[str, str], verified_ticket: Ticket
) -> None:
    response = create(api, user["token"], verified_ticket.id)

    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.entity_id == response.json()["id"])
    ).all()
    assert [row.action for row in rows] == ["listing.created"]
    assert str(rows[0].actor_id) == user["id"]
    assert rows[0].request_id == response.headers["X-Request-ID"]
    assert rows[0].details["asking_price_paise"] == 200_000


def test_someone_elses_ticket_is_404(
    api: TestClient, db_session: Session, user: dict[str, str], published_event: Event
) -> None:
    other = new_account(api)
    ticket = users_ticket(db_session, other, published_event)

    response = create(api, user["token"], ticket.id)

    assert response.status_code == 404
    assert problem_code(response) == "ticket-not-found"


def test_an_admin_listing_another_users_ticket_is_403(
    api: TestClient, admin: dict[str, str], verified_ticket: Ticket
) -> None:
    response = create(api, admin["token"], verified_ticket.id)

    assert response.status_code == 403
    assert problem_code(response) == "owner-only"


@pytest.mark.parametrize(
    "fields",
    [
        {"verification_status": "PENDING", "transfer_eligible": False},
        {"verification_status": "REJECTED", "transfer_eligible": False},
        {"transfer_eligible": False},
        {"status": "VOID"},
    ],
    ids=["pending", "rejected", "not transfer-eligible", "void"],
)
def test_unlistable_tickets_are_409(
    api: TestClient,
    db_session: Session,
    user: dict[str, str],
    published_event: Event,
    fields: dict[str, Any],
) -> None:
    ticket = users_ticket(db_session, user, published_event, **fields)

    response = create(api, user["token"], ticket.id)

    assert response.status_code == 409
    assert problem_code(response) == "ticket-not-listable"


@pytest.mark.parametrize("event_status", ["CANCELLED", "COMPLETED"])
def test_closed_events_are_409(
    api: TestClient, db_session: Session, user: dict[str, str], event_status: str
) -> None:
    ticket = users_ticket(db_session, user, make_event(db_session, status=event_status))

    response = create(api, user["token"], ticket.id)

    assert response.status_code == 409
    assert problem_code(response) == "event-not-open"


def test_a_started_event_is_422(api: TestClient, db_session: Session, user: dict[str, str]) -> None:
    started = make_event(
        db_session, status="PUBLISHED", starts_at=datetime.now(UTC) - timedelta(hours=1)
    )
    ticket = users_ticket(db_session, user, started)

    response = create(api, user["token"], ticket.id)

    assert response.status_code == 422
    assert problem_code(response) == "event-started"


@pytest.mark.parametrize("price", [99, 250_001])
def test_prices_outside_one_rupee_to_face_value_are_422(
    api: TestClient, user: dict[str, str], verified_ticket: Ticket, price: int
) -> None:
    response = create(api, user["token"], verified_ticket.id, asking_price_paise=price)

    assert response.status_code == 422
    assert problem_code(response) == "price-out-of-range"


def test_expiry_after_the_event_start_is_422(
    api: TestClient, user: dict[str, str], verified_ticket: Ticket, published_event: Event
) -> None:
    late = (published_event.starts_at + timedelta(minutes=1)).isoformat()

    response = create(api, user["token"], verified_ticket.id, expires_at=late)

    assert response.status_code == 422
    assert problem_code(response) == "bad-expiry"


def test_expiry_without_a_timezone_is_422(
    api: TestClient, user: dict[str, str], verified_ticket: Ticket
) -> None:
    response = create(api, user["token"], verified_ticket.id, expires_at="2027-01-15T18:30:00")

    assert response.status_code == 422


def test_a_second_open_listing_for_one_ticket_is_409(
    api: TestClient, user: dict[str, str], verified_ticket: Ticket
) -> None:
    assert create(api, user["token"], verified_ticket.id).status_code == 201

    response = create(api, user["token"], verified_ticket.id)

    assert response.status_code == 409
    assert problem_code(response) == "listing-already-open"


def test_a_ticket_can_be_listed_again_after_its_listing_is_cancelled(
    api: TestClient, db_session: Session, user: dict[str, str], verified_ticket: Ticket
) -> None:
    make_listing(db_session, ticket=verified_ticket, status="CANCELLED")

    response = create(api, user["token"], verified_ticket.id)

    assert response.status_code == 201


@pytest.mark.parametrize(
    "extra",
    [{"seller_id": "00000000-0000-4000-8000-000000000000"}, {"status": "ACTIVE"}],
    ids=["seller_id", "status"],
)
def test_seller_and_status_cannot_be_sent(
    api: TestClient, user: dict[str, str], verified_ticket: Ticket, extra: dict[str, str]
) -> None:
    response = create(api, user["token"], verified_ticket.id, **extra)

    assert response.status_code == 422
