"""Purchase evidence: the owner uploads it, the owner and admins open it through short links."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.infrastructure.storage import InMemoryStorage, S3Storage, StorageError
from app.modules.tickets.models import Ticket, TicketDocument
from tests.integration.api import bearer
from tests.integration.files import JPEG_BYTES, PDF_BYTES, PNG_BYTES

pytestmark = pytest.mark.integration


def documents_url(ticket: Ticket | str) -> str:
    ticket_id = ticket if isinstance(ticket, str) else ticket.id
    return f"/api/v1/tickets/{ticket_id}/documents"


def upload(
    api: TestClient,
    account: dict[str, str] | None,
    ticket: Ticket,
    data: bytes = PNG_BYTES,
    name: str = "receipt.png",
    claimed_type: str = "image/png",
) -> Any:
    headers = bearer(account["token"]) if account else {}
    return api.post(
        documents_url(ticket), files={"file": (name, data, claimed_type)}, headers=headers
    )


def problem_code(response: Any) -> str:
    return response.json()["type"].rsplit(":", 1)[-1]


def test_the_owner_uploads_a_png(
    api: TestClient, user: dict[str, str], owned_ticket: Ticket, memory_storage: InMemoryStorage
) -> None:
    response = upload(api, user, owned_ticket)

    assert response.status_code == 201, response.text
    document = response.json()
    assert document["content_type"] == "image/png"
    assert document["size_bytes"] == len(PNG_BYTES)
    assert set(document) == {"id", "content_type", "size_bytes", "created_at"}
    assert list(memory_storage.objects.values()) == [(PNG_BYTES, "image/png")]


def test_a_jpeg_is_accepted(api: TestClient, user: dict[str, str], owned_ticket: Ticket) -> None:
    response = upload(api, user, owned_ticket, JPEG_BYTES, "photo.jpg", "image/jpeg")

    assert response.status_code == 201
    assert response.json()["content_type"] == "image/jpeg"


def test_the_stored_type_comes_from_the_bytes_not_the_name(
    api: TestClient, user: dict[str, str], owned_ticket: Ticket, memory_storage: InMemoryStorage
) -> None:
    response = upload(api, user, owned_ticket, PDF_BYTES, "photo.png", "image/png")

    assert response.status_code == 201
    assert response.json()["content_type"] == "application/pdf"
    assert list(memory_storage.objects.values()) == [(PDF_BYTES, "application/pdf")]


def test_a_text_file_renamed_jpg_is_415(
    api: TestClient, user: dict[str, str], owned_ticket: Ticket, memory_storage: InMemoryStorage
) -> None:
    response = upload(api, user, owned_ticket, b"<script>alert(1)</script>", "x.jpg", "image/jpeg")

    assert response.status_code == 415
    assert problem_code(response) == "unsupported-file-type"
    assert memory_storage.objects == {}


def test_an_empty_file_is_422(api: TestClient, user: dict[str, str], owned_ticket: Ticket) -> None:
    response = upload(api, user, owned_ticket, b"")

    assert response.status_code == 422
    assert problem_code(response) == "empty-file"


def test_a_file_over_5_mib_is_413(
    api: TestClient, user: dict[str, str], owned_ticket: Ticket, memory_storage: InMemoryStorage
) -> None:
    too_big = PNG_BYTES + b"\0" * (5_242_881 - len(PNG_BYTES))

    response = upload(api, user, owned_ticket, too_big)

    assert response.status_code == 413
    assert problem_code(response) == "file-too-large"
    assert memory_storage.objects == {}


def test_a_file_of_exactly_5_mib_is_accepted(
    api: TestClient, user: dict[str, str], owned_ticket: Ticket
) -> None:
    exactly = PNG_BYTES + b"\0" * (5_242_880 - len(PNG_BYTES))

    response = upload(api, user, owned_ticket, exactly)

    assert response.status_code == 201
    assert response.json()["size_bytes"] == 5_242_880


def test_the_key_never_contains_the_filename(
    api: TestClient, user: dict[str, str], owned_ticket: Ticket, db_session: Session
) -> None:
    response = upload(api, user, owned_ticket, name="../../etc/passwd.png")

    assert response.status_code == 201
    key = db_session.scalar(select(TicketDocument.storage_key))
    assert re.fullmatch(r"ticket-documents/[0-9a-f]{32}", key)


def test_a_sixth_document_is_409(
    api: TestClient, user: dict[str, str], owned_ticket: Ticket
) -> None:
    for _ in range(5):
        assert upload(api, user, owned_ticket).status_code == 201

    sixth = upload(api, user, owned_ticket)

    assert sixth.status_code == 409
    assert problem_code(sixth) == "too-many-documents"


def test_uploading_to_a_verified_ticket_is_409(
    api: TestClient, user: dict[str, str], owned_ticket: Ticket, db_session: Session
) -> None:
    db_session.execute(
        update(Ticket).where(Ticket.id == owned_ticket.id).values(verification_status="VERIFIED")
    )

    response = upload(api, user, owned_ticket)

    assert response.status_code == 409
    assert problem_code(response) == "ticket-not-pending"


def test_an_admin_cannot_upload(
    api: TestClient, admin: dict[str, str], owned_ticket: Ticket
) -> None:
    response = upload(api, admin, owned_ticket)

    assert response.status_code == 403
    assert problem_code(response) == "owner-only"


def test_an_anonymous_upload_is_401(api: TestClient, owned_ticket: Ticket) -> None:
    assert upload(api, None, owned_ticket).status_code == 401


def test_a_request_without_a_file_is_422(
    api: TestClient, user: dict[str, str], owned_ticket: Ticket
) -> None:
    response = api.post(documents_url(owned_ticket), headers=bearer(user["token"]))

    assert response.status_code == 422


class BrokenStorage(InMemoryStorage):
    def put_object(self, key: str, data: bytes, content_type: str) -> None:
        raise StorageError("down")


def test_storage_down_gives_503_and_no_row(
    api: TestClient,
    user: dict[str, str],
    owned_ticket: Ticket,
    db_app: FastAPI,
    db_session: Session,
) -> None:
    db_app.state.storage = BrokenStorage()

    response = upload(api, user, owned_ticket)

    assert response.status_code == 503
    assert problem_code(response) == "storage-unavailable"
    assert db_session.scalar(select(func.count()).select_from(TicketDocument)) == 0


def test_the_owner_lists_and_links_documents(
    api: TestClient, user: dict[str, str], owned_ticket: Ticket
) -> None:
    document = upload(api, user, owned_ticket).json()

    listed = api.get(documents_url(owned_ticket), headers=bearer(user["token"]))
    link = api.get(f"{documents_url(owned_ticket)}/{document['id']}", headers=bearer(user["token"]))

    assert [d["id"] for d in listed.json()] == [document["id"]]
    assert link.status_code == 200
    body = link.json()
    assert body["url"].startswith("memory://ticket-documents/")
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert abs(expires_at - (datetime.now(UTC) + timedelta(seconds=300))) < timedelta(seconds=5)


def test_an_admin_lists_and_links_documents(
    api: TestClient, user: dict[str, str], admin: dict[str, str], owned_ticket: Ticket
) -> None:
    document = upload(api, user, owned_ticket).json()

    listed = api.get(documents_url(owned_ticket), headers=bearer(admin["token"]))
    link = api.get(
        f"{documents_url(owned_ticket)}/{document['id']}", headers=bearer(admin["token"])
    )

    assert listed.status_code == 200
    assert link.status_code == 200


def test_an_unknown_document_is_404(
    api: TestClient, user: dict[str, str], owned_ticket: Ticket
) -> None:
    response = api.get(
        f"{documents_url(owned_ticket)}/00000000-0000-4000-8000-000000000000",
        headers=bearer(user["token"]),
    )

    assert response.status_code == 404
    assert problem_code(response) == "document-not-found"


def test_a_real_link_opens(
    api: TestClient,
    user: dict[str, str],
    owned_ticket: Ticket,
    db_app: FastAPI,
    s3_storage: S3Storage,
) -> None:
    db_app.state.storage = s3_storage
    document = upload(api, user, owned_ticket).json()

    link = api.get(f"{documents_url(owned_ticket)}/{document['id']}", headers=bearer(user["token"]))
    opened = httpx.get(link.json()["url"])

    assert opened.status_code == 200
    assert opened.headers["content-type"] == "image/png"
    assert opened.content == PNG_BYTES
