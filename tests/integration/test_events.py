"""Events (FR-02): admins create and manage them, everyone reads published ones."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.events.models import FAKE_PROVIDER_CODE, Event, TicketProvider
from tests.integration.api import bearer
from tests.integration.conftest import new_account
from tests.integration.factories import make_event

pytestmark = pytest.mark.integration

ADMIN_EVENTS = "/api/v1/admin/events"
EVENTS = "/api/v1/events"


def iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def future(days: int = 30) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def fake_provider_id(db_session: Session) -> str:
    provider = db_session.scalar(
        select(TicketProvider.id).where(TicketProvider.code == FAKE_PROVIDER_CODE)
    )
    return str(provider)


def event_body(db_session: Session, **fields: Any) -> dict[str, Any]:
    body = {
        "name": "Sunburn Arena",
        "description": "An evening of electronic music.",
        "category": "CONCERT",
        "venue": "Jawaharlal Nehru Stadium",
        "city": "Kochi",
        "starts_at": iso(future()),
        "organizer": "Percept Live",
        "provider_id": fake_provider_id(db_session),
    }
    return body | fields


def problem_code(response: Any) -> str:
    return response.json()["type"].rsplit(":", 1)[-1]


def patch(api: TestClient, admin: dict[str, str], event_id: Any, **body: Any) -> Any:
    return api.patch(f"{ADMIN_EVENTS}/{event_id}", json=body, headers=bearer(admin["token"]))


# ---------- creating ----------


def test_a_user_cannot_create_an_event(api: TestClient, db_session: Session) -> None:
    user = new_account(api)
    response = api.post(ADMIN_EVENTS, json=event_body(db_session), headers=bearer(user["token"]))

    assert response.status_code == 403
    assert problem_code(response) == "admin-required"


def test_a_guest_cannot_create_an_event(api: TestClient, db_session: Session) -> None:
    assert api.post(ADMIN_EVENTS, json=event_body(db_session)).status_code == 401


def test_an_admin_creates_a_draft_event(
    api: TestClient, admin: dict[str, str], db_session: Session
) -> None:
    body = event_body(db_session)
    response = api.post(ADMIN_EVENTS, json=body, headers=bearer(admin["token"]))

    assert response.status_code == 201, response.text
    event = response.json()
    assert event["status"] == "DRAFT"
    assert event["provider_id"] == body["provider_id"]
    assert event["name"] == "Sunburn Arena"
    assert set(event) == {
        "id",
        "name",
        "description",
        "category",
        "venue",
        "city",
        "starts_at",
        "organizer",
        "provider_id",
        "status",
    }


def test_starts_at_in_the_past_is_422(
    api: TestClient, admin: dict[str, str], db_session: Session
) -> None:
    body = event_body(db_session, starts_at=iso(datetime.now(UTC) - timedelta(hours=1)))
    response = api.post(ADMIN_EVENTS, json=body, headers=bearer(admin["token"]))

    assert response.status_code == 422
    assert problem_code(response) == "event-in-past"


def test_starts_at_without_a_timezone_is_422(
    api: TestClient, admin: dict[str, str], db_session: Session
) -> None:
    naive = future().replace(tzinfo=None).isoformat()
    response = api.post(
        ADMIN_EVENTS, json=event_body(db_session, starts_at=naive), headers=bearer(admin["token"])
    )

    assert response.status_code == 422


def test_a_whitespace_only_name_is_422(
    api: TestClient, admin: dict[str, str], db_session: Session
) -> None:
    response = api.post(
        ADMIN_EVENTS, json=event_body(db_session, name="   "), headers=bearer(admin["token"])
    )

    assert response.status_code == 422


def test_an_unknown_category_is_422(
    api: TestClient, admin: dict[str, str], db_session: Session
) -> None:
    response = api.post(
        ADMIN_EVENTS, json=event_body(db_session, category="OPERA"), headers=bearer(admin["token"])
    )

    assert response.status_code == 422


def test_an_unknown_provider_is_404(
    api: TestClient, admin: dict[str, str], db_session: Session
) -> None:
    body = event_body(db_session, provider_id="00000000-0000-4000-8000-000000000000")
    response = api.post(ADMIN_EVENTS, json=body, headers=bearer(admin["token"]))

    assert response.status_code == 404
    assert problem_code(response) == "provider-not-found"


# ---------- reading ----------


def test_guests_see_published_events_but_not_drafts(api: TestClient, db_session: Session) -> None:
    published = make_event(db_session, status="PUBLISHED")
    draft = make_event(db_session)

    ids = {item["id"] for item in api.get(EVENTS, params={"limit": 100}).json()["items"]}

    assert str(published.id) in ids
    assert str(draft.id) not in ids
    assert api.get(f"{EVENTS}/{published.id}").status_code == 200
    assert api.get(f"{EVENTS}/{draft.id}").status_code == 404


def test_the_admin_list_shows_every_status(
    api: TestClient, admin: dict[str, str], db_session: Session
) -> None:
    made = {
        str(make_event(db_session, status=status).id)
        for status in ("DRAFT", "PUBLISHED", "CANCELLED")
    }

    response = api.get(ADMIN_EVENTS, params={"limit": 100}, headers=bearer(admin["token"]))

    assert response.status_code == 200
    assert made <= {item["id"] for item in response.json()["items"]}


def test_a_user_cannot_list_admin_events(api: TestClient) -> None:
    user = new_account(api)
    assert api.get(ADMIN_EVENTS, headers=bearer(user["token"])).status_code == 403


def test_events_page_through_without_repeats(api: TestClient, db_session: Session) -> None:
    made = {str(make_event(db_session, status="PUBLISHED", city="Pagetown").id) for _ in range(25)}

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 10} | ({"cursor": cursor} if cursor else {})
        page = api.get(EVENTS, params=params).json()
        seen += [item["id"] for item in page["items"] if item["city"] == "Pagetown"]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == len(set(seen)) == 25
    assert set(seen) == made


def test_a_bad_cursor_is_400(api: TestClient) -> None:
    response = api.get(EVENTS, params={"cursor": "garbage"})

    assert response.status_code == 400
    assert problem_code(response) == "bad-cursor"


# ---------- changing ----------


@pytest.mark.parametrize(
    ("start", "to", "expected"),
    [
        ("DRAFT", "PUBLISHED", 200),
        ("DRAFT", "CANCELLED", 200),
        ("PUBLISHED", "CANCELLED", 200),
        ("DRAFT", "COMPLETED", 409),
        ("PUBLISHED", "DRAFT", 409),
        ("CANCELLED", "PUBLISHED", 409),
        ("COMPLETED", "PUBLISHED", 409),
    ],
)
def test_status_changes_follow_the_state_machine(
    api: TestClient,
    admin: dict[str, str],
    db_session: Session,
    start: str,
    to: str,
    expected: int,
) -> None:
    event = make_event(db_session, status=start)

    response = patch(api, admin, event.id, status=to)

    assert response.status_code == expected, response.text
    if expected == 200:
        assert response.json()["status"] == to


def test_completing_needs_the_start_time_to_have_passed(
    api: TestClient, admin: dict[str, str], db_session: Session
) -> None:
    upcoming = make_event(db_session, status="PUBLISHED")
    finished = make_event(
        db_session, status="PUBLISHED", starts_at=datetime.now(UTC) - timedelta(days=1)
    )

    refused = patch(api, admin, upcoming.id, status="COMPLETED")
    done = patch(api, admin, finished.id, status="COMPLETED")

    assert refused.status_code == 409
    assert problem_code(refused) == "illegal-status-change"
    assert done.status_code == 200
    assert done.json()["status"] == "COMPLETED"


def test_a_draft_whose_start_passed_cannot_be_published(
    api: TestClient, admin: dict[str, str], db_session: Session
) -> None:
    stale = make_event(db_session, starts_at=datetime.now(UTC) - timedelta(hours=1))

    response = patch(api, admin, stale.id, status="PUBLISHED")

    assert response.status_code == 409
    assert problem_code(response) == "illegal-status-change"


@pytest.mark.parametrize("status", ["CANCELLED", "COMPLETED"])
def test_cancelled_and_completed_events_are_read_only(
    api: TestClient, admin: dict[str, str], db_session: Session, status: str
) -> None:
    event = make_event(db_session, status=status)

    response = patch(api, admin, event.id, name="Renamed")

    assert response.status_code == 409
    assert problem_code(response) == "event-not-editable"


def test_fields_can_change_while_published(
    api: TestClient, admin: dict[str, str], db_session: Session
) -> None:
    event = make_event(db_session, status="PUBLISHED")
    later = iso(future(60))

    response = patch(api, admin, event.id, venue="New Venue", starts_at=later)

    assert response.status_code == 200
    assert response.json()["venue"] == "New Venue"
    db_session.expire_all()
    assert db_session.get(Event, event.id).venue == "New Venue"


def test_moving_the_start_into_the_past_is_422(
    api: TestClient, admin: dict[str, str], db_session: Session
) -> None:
    event = make_event(db_session)

    response = patch(api, admin, event.id, starts_at=iso(datetime.now(UTC) - timedelta(days=1)))

    assert response.status_code == 422
    assert problem_code(response) == "event-in-past"


def test_the_provider_is_fixed_after_draft(
    api: TestClient, admin: dict[str, str], db_session: Session
) -> None:
    event = make_event(db_session, status="PUBLISHED")

    response = patch(api, admin, event.id, provider_id=fake_provider_id(db_session))

    assert response.status_code == 409
    assert problem_code(response) == "event-not-editable"


def test_patching_an_unknown_event_is_404(api: TestClient, admin: dict[str, str]) -> None:
    response = patch(api, admin, "00000000-0000-4000-8000-000000000000", name="x")

    assert response.status_code == 404
    assert problem_code(response) == "event-not-found"


@pytest.mark.parametrize("field", ["name", "starts_at", "status", "provider_id"])
def test_an_explicit_null_is_422(
    api: TestClient, admin: dict[str, str], db_session: Session, field: str
) -> None:
    event = make_event(db_session)

    response = patch(api, admin, event.id, **{field: None})

    assert response.status_code == 422
