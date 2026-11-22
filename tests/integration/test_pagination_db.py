"""paginate() against Postgres: following next_cursor visits every row exactly once."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pagination import encode_cursor, paginate
from app.modules.events.models import TicketProvider
from tests.integration.factories import make_provider

pytestmark = pytest.mark.integration


def test_following_the_cursor_visits_every_row_once(db_session: Session) -> None:
    made = {make_provider(db_session, code=f"page-{i}").id for i in range(7)}
    stmt = (
        select(TicketProvider)
        .where(TicketProvider.code.like("page-%"))
        .order_by(TicketProvider.code, TicketProvider.id)
    )

    seen, pages, cursor = [], 0, None
    while True:
        rows, cursor = paginate(db_session, stmt, cursor, limit=3)
        seen += [row.id for row in rows]
        pages += 1
        if cursor is None:
            break

    assert pages == 3
    assert len(seen) == 7
    assert set(seen) == made


def test_an_exact_multiple_of_the_limit_has_no_empty_last_page(db_session: Session) -> None:
    for i in range(4):
        make_provider(db_session, code=f"even-{i}")
    stmt = select(TicketProvider).where(TicketProvider.code.like("even-%"))

    first, cursor = paginate(db_session, stmt.order_by(TicketProvider.id), None, limit=2)
    second, end = paginate(db_session, stmt.order_by(TicketProvider.id), cursor, limit=2)

    assert len(first) == 2
    assert len(second) == 2
    assert end is None


@pytest.mark.parametrize("cursor", [encode_cursor(10**30), "A" * 300], ids=["huge", "long"])
def test_a_list_answers_any_bad_cursor_with_400(api: TestClient, cursor: str) -> None:
    response = api.get("/api/v1/events", params={"cursor": cursor})

    assert response.status_code == 400
    assert response.json()["type"].endswith("bad-cursor")
