"""Every (from, to) pair of reservation statuses: only the allowed ones work, each with one audit
row. The same test M5 wrote for listings."""

import itertools

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.modules.audit.models import AuditLog
from app.modules.reservations.errors import (
    IllegalReservationTransitionError,
    ReservationChangedError,
)
from app.modules.reservations.models import Reservation, ReservationStatus
from app.modules.reservations.state import ACTION, ALLOWED, transition
from tests.integration.factories import make_listing, make_reservation

pytestmark = pytest.mark.integration

# docs/state-machines.md, written out by hand: changing either side alone fails the test.
DOC_TABLE = {
    "CREATED": {"PAYMENT_PENDING", "EXPIRED", "CANCELLED"},
    "PAYMENT_PENDING": {"CONFIRMED", "FAILED", "EXPIRED"},
    "CONFIRMED": set(),
    "EXPIRED": set(),
    "FAILED": set(),
    "CANCELLED": set(),
}


def test_allowed_matches_docs_state_machines_md():
    assert {k.value: {s.value for s in v} for k, v in ALLOWED.items()} == DOC_TABLE


@pytest.mark.parametrize("from_, to", list(itertools.product(ReservationStatus, ReservationStatus)))
def test_only_allowed_moves_succeed(db_session, from_, to):
    reservation = make_reservation(db_session, status=from_)
    rows = select(AuditLog).where(
        AuditLog.entity_type == "reservation", AuditLog.entity_id == reservation.id
    )
    if to in ALLOWED[from_]:
        transition(db_session, reservation, to, actor_id=None)
        assert reservation.status == to
        (row,) = db_session.scalars(rows)
        assert row.action == ACTION[(from_, to)]
        assert row.details["from"] == from_ and row.details["to"] == to
    else:
        with pytest.raises(IllegalReservationTransitionError):
            transition(db_session, reservation, to, actor_id=None)
        db_session.refresh(reservation)
        assert reservation.status == from_
        assert db_session.scalar(select(func.count()).select_from(rows.subquery())) == 0


def test_a_reservation_changed_underneath(db_session):
    reservation = make_reservation(db_session, status="CREATED")
    db_session.execute(
        update(Reservation).where(Reservation.id == reservation.id).values(status="EXPIRED"),
        execution_options={"synchronize_session": False},
    )
    with pytest.raises(ReservationChangedError) as caught:
        transition(db_session, reservation, ReservationStatus.CANCELLED, actor_id=None)
    assert caught.value.extensions["current_status"] == "EXPIRED"


@pytest.mark.parametrize("live", ["CREATED", "PAYMENT_PENDING", "CONFIRMED"])
def test_the_database_refuses_a_second_live_reservation(db_session, live):
    listing = make_listing(db_session, status="RESERVED")
    make_reservation(db_session, listing=listing, status=live)
    with pytest.raises(IntegrityError, match="uq_reservations_one_live_per_listing"):
        make_reservation(db_session, listing=listing, status="CREATED")


@pytest.mark.parametrize("over", ["EXPIRED", "FAILED", "CANCELLED"])
def test_a_reservation_that_is_over_leaves_room_for_another(db_session, over):
    listing = make_listing(db_session, status="RESERVED")
    make_reservation(db_session, listing=listing, status=over)
    make_reservation(db_session, listing=listing, status="CREATED")


def test_an_unknown_status_is_refused(db_session):
    with pytest.raises(IntegrityError, match="ck_reservations_status"):
        make_reservation(db_session, status="MAYBE")
