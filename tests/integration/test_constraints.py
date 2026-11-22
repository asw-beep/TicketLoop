"""The database itself refuses rows that break a business rule (docs/database-design.md).

Application code will check these rules first, to give a helpful error. These tests prove the
database is the last line of defence, for the day a bug skips the check or two requests pass
the same check at the same instant. Each test names the exact constraint that must fire.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.tickets.models import Ticket
from tests.integration.factories import (
    make_listing,
    make_password_reset_token,
    make_refresh_token,
    make_ticket,
    make_user,
)

pytestmark = pytest.mark.integration


@contextmanager
def rejected_by(session: Session, constraint: str) -> Iterator[None]:
    """Expect the block to fail with an IntegrityError raised by `constraint`, and no other."""
    with pytest.raises(IntegrityError) as caught:
        yield
        session.flush()
    assert caught.value.orig.diag.constraint_name == constraint
    session.rollback()


# ---------- listings: FR-04 ----------


def test_a_second_open_listing_for_the_same_ticket_is_rejected(db_session: Session) -> None:
    first = make_listing(db_session)
    ticket = db_session.get(Ticket, first.ticket_id)

    with rejected_by(db_session, "uq_listings_one_open_per_ticket"):
        make_listing(db_session, ticket=ticket)


def test_a_ticket_can_be_listed_again_once_its_listing_is_closed(db_session: Session) -> None:
    first = make_listing(db_session)
    first.status = "CANCELLED"
    db_session.flush()

    second = make_listing(db_session, ticket=db_session.get(Ticket, first.ticket_id))

    assert second.id != first.id


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"asking_price_paise": 250_001}, "ck_listings_price_cap"),
        ({"asking_price_paise": 99}, "ck_listings_min_price"),
        ({"status": "REJECTED"}, "ck_listings_rejection_reason"),
        ({"status": "ON_HOLD"}, "ck_listings_status"),
    ],
    ids=[
        "above the original price",
        "under one rupee",
        "rejected without a reason",
        "unknown status",
    ],
)
def test_a_listing_that_breaks_a_rule_is_rejected(
    db_session: Session, fields: dict[str, object], constraint: str
) -> None:
    with rejected_by(db_session, constraint):
        make_listing(db_session, **fields)  # the factory's ticket costs 250,000 paise


# ---------- users: FR-01 ----------


def test_two_accounts_cannot_share_an_email(db_session: Session) -> None:
    make_user(db_session, email="asha@example.in")

    with rejected_by(db_session, "uq_users_email"):
        make_user(db_session, email="asha@example.in")


def test_an_email_must_be_stored_lowercased(db_session: Session) -> None:
    with rejected_by(db_session, "ck_users_email_lowercase"):
        make_user(db_session, email="Asha@Example.in")


# ---------- refresh and reset tokens: FR-01 ----------


def test_a_refresh_token_hash_is_stored_once(db_session: Session) -> None:
    first = make_refresh_token(db_session)

    with rejected_by(db_session, "uq_refresh_tokens_token_hash"):
        make_refresh_token(db_session, token_hash=first.token_hash)


def test_a_replaced_refresh_token_must_also_be_revoked(db_session: Session) -> None:
    newer = make_refresh_token(db_session)

    with rejected_by(db_session, "ck_refresh_tokens_replaced_means_revoked"):
        make_refresh_token(db_session, replaced_by=newer.id)  # revoked_at left NULL


def test_a_refresh_token_must_belong_to_a_real_user(db_session: Session) -> None:
    with rejected_by(db_session, "fk_refresh_tokens_user_id_users"):
        make_refresh_token(db_session, user_id=uuid.uuid4())


def test_a_password_reset_token_hash_is_stored_once(db_session: Session) -> None:
    first = make_password_reset_token(db_session)

    with rejected_by(db_session, "uq_password_reset_tokens_token_hash"):
        make_password_reset_token(db_session, token_hash=first.token_hash)


# ---------- tickets: FR-03 ----------


def test_one_real_ticket_cannot_be_registered_twice(db_session: Session) -> None:
    ticket = make_ticket(db_session)

    with rejected_by(db_session, "uq_tickets_event_provider_reference"):
        make_ticket(
            db_session, event_id=ticket.event_id, provider_reference=ticket.provider_reference
        )


# ---------- audit log: FR-12 ----------


@pytest.mark.parametrize("statement", [update, delete], ids=["update", "delete"])
def test_audit_rows_can_never_be_changed(db_session: Session, statement: object) -> None:
    row = AuditLog(entity_type="listing", entity_id=uuid.uuid4(), action="listing.approved")
    db_session.add(row)
    db_session.flush()

    change = statement(AuditLog).where(AuditLog.id == row.id)
    if statement is update:
        change = change.values(action="listing.rejected")

    with pytest.raises(DBAPIError, match="audit_logs is append-only"):
        db_session.execute(change)
