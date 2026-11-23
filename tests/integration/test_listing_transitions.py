"""M5's "done when": every (from, to) pair of listing statuses, and only the allowed ones work.

Each listing is inserted directly in its starting status (bypassing the API), then moved with
`transition()`. An allowed move changes the status and writes exactly one audit row; any other
move raises and leaves both the listing and the audit log as they were.
"""

import itertools
import uuid

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.listings.errors import IllegalListingTransitionError, ListingChangedError
from app.modules.listings.models import Listing, ListingStatus
from app.modules.listings.state import ACTION, ALLOWED, transition
from tests.integration.factories import make_listing

pytestmark = pytest.mark.integration

# docs/state-machines.md, written out by hand: changing either side alone fails the test.
DOC_TABLE = {
    "DRAFT": {"PENDING_VERIFICATION", "CANCELLED"},
    "PENDING_VERIFICATION": {"ACTIVE", "REJECTED", "CANCELLED"},
    "ACTIVE": {"RESERVED", "CANCELLED", "EXPIRED"},
    "RESERVED": {"ACTIVE", "EXPIRED", "SOLD", "CANCELLED"},
    "SOLD": set(),
    "REJECTED": set(),
    "CANCELLED": set(),
    "EXPIRED": set(),
}


def audit_rows(db: Session, listing_id: uuid.UUID) -> list[AuditLog]:
    return list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.entity_type == "listing", AuditLog.entity_id == listing_id)
            .order_by(AuditLog.created_at, AuditLog.id)
        )
    )


def audit_count(db: Session, listing_id: uuid.UUID) -> int:
    return db.scalar(
        select(func.count()).where(
            AuditLog.entity_type == "listing", AuditLog.entity_id == listing_id
        )
    )


def test_allowed_matches_docs_state_machines_md():
    assert {k.value: {s.value for s in v} for k, v in ALLOWED.items()} == DOC_TABLE


def test_every_allowed_move_has_an_audit_action():
    assert set(ACTION) == {(f, t) for f, tos in ALLOWED.items() for t in tos}


@pytest.mark.parametrize("from_, to", list(itertools.product(ListingStatus, ListingStatus)))
def test_only_allowed_moves_succeed(db_session, from_, to):
    listing = make_listing(db_session, status=from_)
    before = audit_count(db_session, listing.id)
    # ck_listings_rejection_reason: a move to REJECTED must carry its reason.
    values = {"rejection_reason": "Test reason."} if to == "REJECTED" else None
    if to in ALLOWED[from_]:
        transition(db_session, listing, to, actor_id=None, values=values)
        assert listing.status == to
        rows = audit_rows(db_session, listing.id)
        assert len(rows) == before + 1
        assert rows[-1].action == ACTION[(from_, to)]
        assert rows[-1].details["from"] == from_ and rows[-1].details["to"] == to
    else:
        with pytest.raises(IllegalListingTransitionError):
            transition(db_session, listing, to, actor_id=None, values=values)
        db_session.refresh(listing)
        assert listing.status == from_
        assert audit_count(db_session, listing.id) == before


def test_a_listing_changed_underneath_gives_listing_changed(db_session):
    listing = make_listing(db_session, status="ACTIVE")
    db_session.execute(  # another transaction got there first; our object still says ACTIVE
        update(Listing).where(Listing.id == listing.id).values(status="CANCELLED"),
        execution_options={"synchronize_session": False},
    )
    with pytest.raises(ListingChangedError) as caught:
        transition(db_session, listing, ListingStatus.EXPIRED, actor_id=None)
    assert caught.value.extensions["current_status"] == "CANCELLED"


def test_a_failure_later_in_the_transaction_undoes_the_change_and_its_audit_row(db_session):
    listing = make_listing(db_session, status="PENDING_VERIFICATION")
    savepoint = db_session.begin_nested()
    transition(db_session, listing, ListingStatus.ACTIVE, actor_id=None)
    with pytest.raises(IntegrityError):  # ck_listings_min_price
        db_session.execute(
            update(Listing).where(Listing.id == listing.id).values(asking_price_paise=1)
        )
    savepoint.rollback()
    db_session.refresh(listing)
    assert listing.status == "PENDING_VERIFICATION"
    assert audit_count(db_session, listing.id) == 0
