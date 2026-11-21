"""get_db: one session per request, committed on success, rolled back on any error.

The routes below exist only in these tests. They use the real get_db through the real app.
"""

from collections.abc import Iterator

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.database.session import DbSession
from app.modules.events.models import TicketProvider
from tests.integration.factories import make_provider

pytestmark = pytest.mark.integration

router = APIRouter()


@router.post("/test-only/providers/{code}")
def add_provider(code: str, db: DbSession) -> dict[str, str]:
    db.add(TicketProvider(code=code, name=code))  # no flush: the INSERT runs at commit
    return {"code": code}


@router.post("/test-only/providers/{code}/then-fail")
def add_provider_then_fail(code: str, db: DbSession) -> None:
    db.add(TicketProvider(code=code, name=code))
    db.flush()  # the INSERT has run inside the transaction
    raise RuntimeError("something failed after the write")


@pytest.fixture
def client(db_app: FastAPI) -> Iterator[TestClient]:
    db_app.include_router(router)
    with TestClient(db_app, raise_server_exceptions=False) as test_client:
        yield test_client


def provider_exists(session: Session, code: str) -> bool:
    found = session.scalar(select(TicketProvider.id).where(TicketProvider.code == code))
    return found is not None


def test_changes_are_committed_when_the_request_succeeds(
    client: TestClient, db_session: Session
) -> None:
    response = client.post("/test-only/providers/committed")

    assert response.status_code == 200
    assert provider_exists(db_session, "committed")


def test_changes_are_rolled_back_when_the_request_fails(
    client: TestClient, db_session: Session
) -> None:
    response = client.post("/test-only/providers/rolled-back/then-fail")

    assert response.status_code == 500
    assert not provider_exists(db_session, "rolled-back")


def test_a_failed_commit_is_reported_as_an_error_not_a_success(
    client: TestClient, db_session: Session
) -> None:
    make_provider(db_session, code="taken")
    db_session.commit()

    # The duplicate code only fails at COMMIT. If get_db committed after the response was
    # sent (FastAPI's default for yield dependencies), the client would already have a 200.
    response = client.post("/test-only/providers/taken")

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
