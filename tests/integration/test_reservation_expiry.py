"""expire_reservations() and scripts/expire_reservations.py: unpaid holds past their end close, and
their tickets go back on sale. Two of them running together is in tests/concurrency/."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Connection, select
from sqlalchemy.orm import Session

import scripts.expire_reservations
from app.core.config import Settings
from app.infrastructure.database.session import create_session_factory
from app.modules.audit.models import AuditLog
from app.modules.listings.models import Listing
from app.modules.reservations.models import Reservation
from app.modules.reservations.service import expire_reservations
from tests.integration.factories import make_listing, make_reservation

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)
PAST = NOW - timedelta(seconds=1)
FUTURE = NOW + timedelta(minutes=5)


def rows(db: Session, entity_id: object) -> list[AuditLog]:
    return list(db.scalars(select(AuditLog).where(AuditLog.entity_id == entity_id)))


def test_a_due_hold_expires_and_its_listing_goes_back_on_sale(db_session: Session) -> None:
    due = make_reservation(db_session, expires_at=PAST)
    not_due = make_reservation(db_session, expires_at=FUTURE)

    assert expire_reservations(db_session, now=NOW) == (1, 1)

    assert due.status == "EXPIRED"
    assert due.listing.status == "ACTIVE"
    assert (not_due.status, not_due.listing.status) == ("CREATED", "RESERVED")
    (reservation_row,) = rows(db_session, due.id)
    assert (reservation_row.action, reservation_row.actor_id) == ("reservation.expired", None)
    (listing_row,) = rows(db_session, due.listing_id)
    assert (listing_row.action, listing_row.actor_id) == ("listing.released", None)
    assert listing_row.details["reason"] == "hold_expired"


def test_a_hold_ends_at_its_expiry_not_after_it(db_session: Session) -> None:
    due = make_reservation(db_session, expires_at=NOW)
    expire_reservations(db_session, now=NOW)
    assert due.status == "EXPIRED"


def test_a_listing_whose_own_end_passed_while_held_expires_too(db_session: Session) -> None:
    listing = make_listing(db_session, status="RESERVED", expires_at=PAST)
    make_reservation(db_session, listing=listing, expires_at=PAST)

    expire_reservations(db_session, now=NOW)

    assert listing.status == "EXPIRED"


@pytest.mark.parametrize(
    "status", ["PAYMENT_PENDING", "CONFIRMED", "EXPIRED", "FAILED", "CANCELLED"]
)
def test_only_unpaid_holds_expire_never_one_being_paid_for(
    db_session: Session, status: str
) -> None:
    listing = make_listing(db_session, status="RESERVED")
    reservation = make_reservation(db_session, listing=listing, status=status, expires_at=PAST)

    assert expire_reservations(db_session, now=NOW) == (0, 0)

    db_session.refresh(listing)
    assert (reservation.status, listing.status) == (status, "RESERVED")
    assert rows(db_session, reservation.id) == []


def test_one_broken_row_does_not_stop_the_rest(db_session: Session) -> None:
    # Its listing is not RESERVED, which no code path can produce; if one ever does, the other
    # buyers' tickets must still come back on sale.
    broken = make_reservation(
        db_session,
        listing=make_listing(db_session, status="SOLD"),
        expires_at=PAST - timedelta(minutes=1),
    )
    fine = make_reservation(db_session, expires_at=PAST)

    assert expire_reservations(db_session, now=NOW) == (2, 1)

    for row in (broken, fine, broken.listing, fine.listing):
        db_session.refresh(row)
    assert (broken.status, broken.listing.status) == ("CREATED", "SOLD")
    assert (fine.status, fine.listing.status) == ("EXPIRED", "ACTIVE")
    assert rows(db_session, broken.id) == []


def test_a_pass_works_through_every_batch(db_connection: Connection) -> None:
    factory = create_session_factory(db_connection)
    factory.configure(join_transaction_mode="create_savepoint")
    with factory() as session, session.begin():
        for _ in range(5):
            make_reservation(session, expires_at=PAST)

    assert scripts.expire_reservations.run_once(factory, batch=2) == 5

    with factory() as session:
        statuses = session.scalars(select(Reservation.status)).all()
        assert set(statuses) == {"EXPIRED"}
        assert set(session.scalars(select(Listing.status)).all()) == {"ACTIVE"}


def test_a_pass_ends_when_nothing_left_can_expire(db_connection: Connection) -> None:
    factory = create_session_factory(db_connection)
    factory.configure(join_transaction_mode="create_savepoint")
    with factory() as session, session.begin():
        for _ in range(2):
            make_reservation(session, listing=make_listing(session, status="SOLD"), expires_at=PAST)

    assert scripts.expire_reservations.run_once(factory, batch=2) == 0


def test_the_script_runs(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(scripts.expire_reservations, "get_settings", lambda: test_settings)

    assert scripts.expire_reservations.main(["--once"]) == 0
    out = capsys.readouterr().out
    expected = r"Expired \d+ reservation\(s\)\.\nSettled \d+ overdue payment\(s\)\.\n"
    assert re.fullmatch(expected, out)
