"""expire_listings() and scripts/expire_listings.py: ACTIVE listings past their expiry close.

Only ACTIVE listings expire. A pending one past its expiry stays in the review queue, where
approve refuses it; the state machine has no PENDING_VERIFICATION to EXPIRED move.
"""

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import scripts.expire_listings
from app.core.config import Settings
from app.modules.audit.models import AuditLog
from app.modules.listings.models import Listing
from app.modules.listings.service import expire_listings
from tests.integration.factories import make_listing

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)
PAST = NOW - timedelta(minutes=5)
FUTURE = NOW + timedelta(days=1)


def expired_rows(db: Session, listing: Listing) -> list[AuditLog]:
    return list(
        db.scalars(
            select(AuditLog).where(
                AuditLog.entity_id == listing.id, AuditLog.action == "listing.expired"
            )
        )
    )


def test_active_listings_past_their_expiry_expire(db_session: Session) -> None:
    due = [make_listing(db_session, status="ACTIVE", expires_at=PAST) for _ in range(2)]
    not_due = make_listing(db_session, status="ACTIVE", expires_at=FUTURE)
    draft = make_listing(db_session, status="DRAFT", expires_at=PAST)
    pending = make_listing(db_session, status="PENDING_VERIFICATION", expires_at=PAST)

    count = expire_listings(db_session, now=NOW)

    assert count == 2
    for listing in due:
        assert listing.status == "EXPIRED"
        (row,) = expired_rows(db_session, listing)
        assert row.actor_id is None
    for listing, status in (
        (not_due, "ACTIVE"),
        (draft, "DRAFT"),
        (pending, "PENDING_VERIFICATION"),
    ):
        db_session.refresh(listing)
        assert listing.status == status
        assert expired_rows(db_session, listing) == []


def test_a_second_run_expires_nothing(db_session: Session) -> None:
    listing = make_listing(db_session, status="ACTIVE", expires_at=PAST)
    assert expire_listings(db_session, now=NOW) == 1

    assert expire_listings(db_session, now=NOW) == 0
    assert len(expired_rows(db_session, listing)) == 1


def test_the_script_runs(
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The script opens its own connection, so it cannot see this test's rolled-back rows; the
    # tests above cover what it expires. This one proves it runs end to end and reports.
    monkeypatch.setattr(scripts.expire_listings, "get_settings", lambda: test_settings)

    assert scripts.expire_listings.main([]) == 0
    assert re.fullmatch(r"Expired \d+ listing\(s\)\.\n", capsys.readouterr().out)
