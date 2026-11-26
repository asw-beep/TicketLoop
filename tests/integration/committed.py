"""Fixtures for tests whose requests must really commit: races between two requests.

Everywhere else a test runs inside one transaction that is rolled back, which is cheap but
means two requests never contend for a row lock. Here each request gets its own connection and
commits, so Postgres decides the race exactly as in production.

`committed_rows` deletes every row the test created afterwards, except audit rows: the
append-only trigger refuses DELETE, they have no foreign keys to keep anything alive, and the
test database is rebuilt on every run.

Import the fixtures into a test module to use them:

    from tests.integration.committed import committed_rows, committing_api  # noqa: F401
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import create_app
from app.modules.events.models import Event, TicketProvider
from app.modules.idempotency.models import IdempotencyKeyRow
from app.modules.identity.models import PasswordResetToken, RefreshToken, User
from app.modules.listings.models import Listing
from app.modules.orders.models import Order
from app.modules.payments.models import Payment
from app.modules.reservations.models import Reservation
from app.modules.tickets.models import Ticket, TicketDocument

# Children before parents, so no foreign key blocks a delete.
CLEANUP_ORDER = (
    Payment,
    Order,
    IdempotencyKeyRow,
    Reservation,
    Listing,
    TicketDocument,
    Ticket,
    Event,
    TicketProvider,
    RefreshToken,
    PasswordResetToken,
    User,
)


@pytest.fixture
def committing_api(test_settings: Settings) -> Iterator[TestClient]:
    """A client for an app with its own engine and pool: every request commits for real."""
    with TestClient(create_app(test_settings)) as client:
        yield client


@pytest.fixture
def committed_rows(test_engine: Engine) -> Iterator[Session]:
    """A session whose commits are real, and a cleanup of every row created meanwhile."""
    with Session(test_engine) as session:
        started = session.scalar(select(func.clock_timestamp()))
        session.rollback()  # later transactions, and their now(), all start after `started`
        yield session
        session.rollback()
        with session.begin():
            for model in CLEANUP_ORDER:
                session.execute(delete(model).where(model.created_at >= started))
