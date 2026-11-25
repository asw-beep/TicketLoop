"""paginate() against Postgres: following next_cursor visits every row exactly once, in order,
however many rows share a sort value and whatever is inserted meanwhile."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Select, case, select
from sqlalchemy.orm import Session

from app.core.pagination import Sort, encode_cursor, paginate
from app.modules.events.models import Event, TicketProvider
from app.modules.listings.models import Listing
from tests.integration.factories import make_event, make_listing, make_provider

pytestmark = pytest.mark.integration

BY_CODE = Sort("code", (TicketProvider.code, TicketProvider.id))


def walk[T](db: Session, stmt: Select[tuple[T]], sort: Sort, limit: int) -> list[list[T]]:
    pages, cursor = [], None
    while True:
        rows, cursor = paginate(db, stmt, sort, cursor, limit)
        pages.append(rows)
        if cursor is None:
            return pages


def test_following_the_cursor_visits_every_row_once(db_session: Session) -> None:
    made = [make_provider(db_session, code=f"page-{i}").id for i in range(7)]
    stmt = select(TicketProvider).where(TicketProvider.code.like("page-%"))

    pages = walk(db_session, stmt, BY_CODE, limit=3)

    assert [len(page) for page in pages] == [3, 3, 1]
    assert [row.id for page in pages for row in page] == made


def test_an_exact_multiple_of_the_limit_has_no_empty_last_page(db_session: Session) -> None:
    for i in range(4):
        make_provider(db_session, code=f"even-{i}")
    stmt = select(TicketProvider).where(TicketProvider.code.like("even-%"))

    assert [len(page) for page in walk(db_session, stmt, BY_CODE, limit=2)] == [2, 2]


@pytest.mark.parametrize("descending", [False, True], ids=["ascending", "descending"])
def test_rows_that_tie_are_neither_repeated_nor_skipped(
    db_session: Session, descending: bool
) -> None:
    event = make_event(db_session)
    prices = [200_000, 100_000, 200_000, 200_000, 100_000, 300_000, 200_000]
    for price in prices:
        make_listing(
            db_session, event_id=event.id, asking_price_paise=price, original_price_paise=300_000
        )
    stmt = select(Listing).where(Listing.event_id == event.id)
    sort = Sort("price", (Listing.asking_price_paise, Listing.id), descending=descending)

    rows = [row for page in walk(db_session, stmt, sort, limit=2) for row in page]

    assert [row.asking_price_paise for row in rows] == sorted(prices, reverse=descending)
    assert len({row.id for row in rows}) == len(prices)
    assert [(row.asking_price_paise, row.id) for row in rows] == sorted(
        ((row.asking_price_paise, row.id) for row in rows), reverse=descending
    )


def test_a_row_inserted_before_the_cursor_does_not_shift_the_next_page(db_session: Session) -> None:
    # The bug an offset has: something new at the front pushes the last row of page 1 onto
    # page 2, so a reader sees it twice.
    start = datetime.now(UTC) + timedelta(days=10)
    for day in range(4):
        make_event(db_session, name="keyset", starts_at=start + timedelta(days=day))
    stmt = select(Event).where(Event.name == "keyset")
    sort = Sort("starts", (Event.starts_at, Event.id))
    first, cursor = paginate(db_session, stmt, sort, None, limit=2)

    make_event(db_session, name="keyset", starts_at=start - timedelta(days=1))
    second, _ = paginate(db_session, stmt, sort, cursor, limit=2)

    assert {row.id for row in first}.isdisjoint({row.id for row in second})
    assert [row.starts_at for row in second] == [
        start + timedelta(days=2),
        start + timedelta(days=3),
    ]


def test_a_computed_key_sorts_and_pages_too(db_session: Session) -> None:
    # reservations/me (M7) puts live ones first: the first key is an expression, not a column.
    event = make_event(db_session)
    for status in ("ACTIVE", "CANCELLED", "ACTIVE", "CANCELLED", "ACTIVE"):
        make_listing(db_session, event_id=event.id, status=status)
    live = case((Listing.status == "ACTIVE", 1), else_=0)
    sort = Sort("live", (live, Listing.created_at, Listing.id), descending=True)
    stmt = select(Listing).where(Listing.event_id == event.id)

    rows = [row for page in walk(db_session, stmt, sort, limit=2) for row in page]

    assert [row.status for row in rows] == ["ACTIVE"] * 3 + ["CANCELLED"] * 2
    assert len({row.id for row in rows}) == 5


@pytest.mark.parametrize(
    "cursor",
    [
        "A" * 300,
        encode_cursor(BY_CODE, ("x", uuid.uuid4())),
        "eyJvIjo0MH0",  # {"o":40}: an offset cursor from before M6
    ],
    ids=["long", "another list's", "old offset"],
)
def test_a_list_answers_any_bad_cursor_with_400(api: TestClient, cursor: str) -> None:
    response = api.get("/api/v1/events", params={"cursor": cursor})

    assert response.status_code == 400
    assert response.json()["type"].endswith("bad-cursor")
