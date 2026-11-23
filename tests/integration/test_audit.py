"""The audit helper: one row per important change, in the same transaction as the change."""

import uuid
from datetime import UTC, datetime

import pytest
import structlog
from sqlalchemy import func, select

from app.modules.audit.models import AuditLog
from app.modules.audit.service import record

pytestmark = pytest.mark.integration


def test_record_reads_the_request_id_from_the_log_context(db_session):
    structlog.contextvars.bind_contextvars(request_id="req-123")
    try:
        row = record(
            db_session,
            actor_id=None,
            entity_type="listing",
            entity_id=uuid.uuid4(),
            action="listing.created",
        )
    finally:
        structlog.contextvars.clear_contextvars()
    assert row.request_id == "req-123"


def test_outside_a_request_the_request_id_is_null(db_session):
    structlog.contextvars.clear_contextvars()
    row = record(
        db_session,
        actor_id=None,
        entity_type="listing",
        entity_id=uuid.uuid4(),
        action="listing.expired",
    )
    assert row.request_id is None


def test_details_are_stored_as_plain_json(db_session):
    some_id, when = uuid.uuid4(), datetime(2027, 1, 15, 13, 0, tzinfo=UTC)
    row = record(
        db_session,
        actor_id=None,
        entity_type="listing",
        entity_id=uuid.uuid4(),
        action="listing.created",
        details={"ticket": some_id, "at": when, "n": 3},
    )
    db_session.expire(row)
    assert row.details == {"ticket": str(some_id), "at": when.isoformat(), "n": 3}


def test_a_rolled_back_savepoint_takes_its_audit_row_with_it(db_session):
    entity_id = uuid.uuid4()
    savepoint = db_session.begin_nested()
    record(
        db_session,
        actor_id=None,
        entity_type="listing",
        entity_id=entity_id,
        action="listing.created",
    )
    savepoint.rollback()
    count = db_session.scalar(select(func.count()).where(AuditLog.entity_id == entity_id))
    assert count == 0


def test_rows_written_in_one_transaction_keep_their_order(db_session):
    entity_id = uuid.uuid4()
    first = record(
        db_session,
        actor_id=None,
        entity_type="listing",
        entity_id=entity_id,
        action="listing.created",
    )
    second = record(
        db_session,
        actor_id=None,
        entity_type="listing",
        entity_id=entity_id,
        action="listing.submitted",
    )
    assert second.created_at > first.created_at
