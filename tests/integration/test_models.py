"""What every row gets for free from the base model: ids and timestamps from Postgres."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session

from app.infrastructure.database.session import create_session_factory
from app.modules.identity.models import User
from tests.integration.factories import make_user

pytestmark = pytest.mark.integration


def test_postgres_generates_the_id(db_session: Session) -> None:
    user = make_user(db_session)

    assert isinstance(user.id, uuid.UUID)


def test_server_defaults_are_loaded_after_insert(db_session: Session) -> None:
    user = make_user(db_session)

    assert (user.role, user.account_status) == ("USER", "ACTIVE")


def test_timestamps_are_timezone_aware_utc(db_session: Session) -> None:
    user = make_user(db_session)

    assert user.created_at.utcoffset() == timedelta(0)
    assert user.updated_at == user.created_at


def test_updated_at_moves_forward_and_is_readable_after_the_session_closes(
    test_engine: Engine,
) -> None:
    # Real commits in separate transactions: now() is fixed for one transaction, so the
    # rolled-back-per-test session would always see the same time.
    factory = create_session_factory(test_engine)
    with factory() as session:
        user = make_user(session)
        session.commit()
    inserted_at = user.updated_at

    try:
        with factory() as session:
            user = session.get(User, user.id)
            user.full_name = "Renamed"
            session.commit()
        # The session is closed. An API response is serialised after get_db closes the
        # session, so the new value must already be loaded, not fetched lazily.
        assert user.updated_at > inserted_at
    finally:
        with test_engine.begin() as connection:
            connection.execute(delete(User).where(User.id == user.id))
