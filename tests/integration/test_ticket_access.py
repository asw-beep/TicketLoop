"""M4's "done when": no user can reach another user's ticket or its files (IDOR).

Every route that takes a ticket or document ID is listed below, and a guard test fails if a
new one is added without being listed, so it cannot skip these checks. For each route:
another user gets 404 with exactly the body a non-existent ID gets, a guest gets 401, and an
admin can read.
"""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.events.models import Event
from tests.integration.api import bearer
from tests.integration.conftest import new_account
from tests.integration.factories import make_ticket
from tests.integration.files import PNG_BYTES

pytestmark = pytest.mark.integration

IDOR_ROUTES = {
    ("GET", "/api/v1/tickets/{ticket_id}"),
    ("POST", "/api/v1/tickets/{ticket_id}/documents"),
    ("GET", "/api/v1/tickets/{ticket_id}/documents"),
    ("GET", "/api/v1/tickets/{ticket_id}/documents/{document_id}"),
}
UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def alice(api: TestClient) -> dict[str, str]:
    return new_account(api)


@pytest.fixture
def bob(api: TestClient) -> dict[str, str]:
    return new_account(api)


def own_ticket(db_session: Session, owner: dict[str, str], event: Event) -> str:
    ticket = make_ticket(
        db_session, event_id=event.id, original_owner_id=owner["id"], current_owner_id=owner["id"]
    )
    return str(ticket.id)


def upload_png(api: TestClient, owner: dict[str, str], ticket_id: str) -> str:
    response = api.post(
        f"/api/v1/tickets/{ticket_id}/documents",
        files={"file": ("receipt.png", PNG_BYTES, "image/png")},
        headers=bearer(owner["token"]),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def alices_ticket_and_document(
    api: TestClient,
    db_session: Session,
    alice: dict[str, str],
    published_event: Event,
    memory_storage: Any,
) -> dict[str, str]:
    ticket_id = own_ticket(db_session, alice, published_event)
    return {"ticket_id": ticket_id, "document_id": upload_png(api, alice, ticket_id)}


def call(
    api: TestClient, method: str, path: str, ids: dict[str, str], account: dict[str, str] | None
) -> Any:
    url = path.format(**ids)
    headers = bearer(account["token"]) if account else {}
    if method == "POST":
        files = {"file": ("receipt.png", PNG_BYTES, "image/png")}
        return api.post(url, files=files, headers=headers)
    return api.get(url, headers=headers)


def comparable(response: Any) -> dict[str, Any]:
    """The parts of a problem body that must not differ between "missing" and "not yours"."""
    body = response.json()
    return {key: body[key] for key in ("type", "title", "status", "detail")}


def test_every_ticket_route_is_covered(db_app: FastAPI) -> None:
    # Read from the OpenAPI schema: FastAPI 0.163 no longer lists included routes flat in
    # app.routes, and the schema is the public description of every route anyway.
    found = {
        (method.upper(), path)
        for path, operations in db_app.openapi()["paths"].items()
        if ("{ticket_id}" in path or "{document_id}" in path) and "/admin/" not in path
        for method in operations
    }

    assert found == IDOR_ROUTES


@pytest.mark.parametrize(("method", "path"), sorted(IDOR_ROUTES))
def test_another_user_gets_404(
    api: TestClient,
    method: str,
    path: str,
    alices_ticket_and_document: dict[str, str],
    bob: dict[str, str],
) -> None:
    response = call(api, method, path, alices_ticket_and_document, bob)
    nonexistent = call(api, method, path, {"ticket_id": UNKNOWN_ID, "document_id": UNKNOWN_ID}, bob)

    assert response.status_code == 404
    assert response.json()["type"].endswith("ticket-not-found")
    assert comparable(response) == comparable(nonexistent)


@pytest.mark.parametrize(("method", "path"), sorted(IDOR_ROUTES))
def test_a_guest_gets_401(
    api: TestClient, method: str, path: str, alices_ticket_and_document: dict[str, str]
) -> None:
    assert call(api, method, path, alices_ticket_and_document, None).status_code == 401


def test_bob_never_sees_alices_ticket_in_his_list(
    api: TestClient, alices_ticket_and_document: dict[str, str], bob: dict[str, str]
) -> None:
    mine = api.get("/api/v1/tickets/me", headers=bearer(bob["token"])).json()["items"]

    assert alices_ticket_and_document["ticket_id"] not in {t["id"] for t in mine}


def test_bobs_upload_attempt_stores_nothing(
    api: TestClient,
    alices_ticket_and_document: dict[str, str],
    bob: dict[str, str],
    memory_storage: Any,
) -> None:
    before = len(memory_storage.objects)

    call(api, "POST", "/api/v1/tickets/{ticket_id}/documents", alices_ticket_and_document, bob)

    assert len(memory_storage.objects) == before


def test_an_admin_reads_alices_ticket_and_documents(
    api: TestClient, admin: dict[str, str], alices_ticket_and_document: dict[str, str]
) -> None:
    for method, path in IDOR_ROUTES:
        if method == "GET":
            response = call(api, method, path, alices_ticket_and_document, admin)
            assert response.status_code == 200, path


def test_alices_document_under_her_other_ticket_is_404(
    api: TestClient,
    db_session: Session,
    alice: dict[str, str],
    published_event: Event,
    alices_ticket_and_document: dict[str, str],
) -> None:
    other_ticket = own_ticket(db_session, alice, published_event)

    response = api.get(
        f"/api/v1/tickets/{other_ticket}/documents/{alices_ticket_and_document['document_id']}",
        headers=bearer(alice["token"]),
    )

    assert response.status_code == 404
