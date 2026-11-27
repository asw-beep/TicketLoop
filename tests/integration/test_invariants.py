"""scripts/check_invariants.sql: the rules a double sale would break, checked on the rows. Each
query is proven to find its violation, planted with the constraint that forbids it dropped
inside the test's own transaction, which is rolled back afterwards."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.identity.models import User
from app.modules.listings.models import Listing
from app.modules.orders.models import Order
from app.modules.tickets.models import Ticket
from scripts.load_test import Violation, violations
from tests.integration.factories import (
    make_listing,
    make_order,
    make_payment,
    make_reservation,
    make_user,
    make_verified_ticket,
)

pytestmark = pytest.mark.integration


def found(db: Session, subjects: set[uuid.UUID]) -> list[tuple[str, str]]:
    """The violations about these rows: other tests may have committed rows of their own."""
    names = {str(subject) for subject in subjects}
    return sorted(
        (row.invariant, row.subject) for row in violations(db.connection()) if row.subject in names
    )


def bought(
    db: Session, ticket: Ticket, buyer: User, *, completed_at: datetime | None = None
) -> tuple[Listing, Order]:
    """A finished sale of this ticket to this buyer: listing SOLD, hold confirmed, order
    COMPLETED, payment SUCCEEDED, and the ticket now the buyer's."""
    listing = make_listing(db, ticket=ticket, seller_id=ticket.current_owner_id, status="SOLD")
    hold = make_reservation(db, listing=listing, status="CONFIRMED", buyer_id=buyer.id)
    stamp = {"updated_at": completed_at} if completed_at else {}
    order = make_order(db, reservation=hold, status="COMPLETED", **stamp)
    make_payment(db, order=order, status="SUCCEEDED")
    ticket.current_owner_id = buyer.id
    db.flush()
    return listing, order


def test_a_clean_purchase_breaks_no_invariant(db_session: Session) -> None:
    ticket = make_verified_ticket(db_session)
    listing, order = bought(db_session, ticket, make_user(db_session))

    assert found(db_session, {ticket.id, listing.id, order.id}) == []


def test_a_ticket_resold_to_someone_else_is_owned_by_the_last_buyer(db_session: Session) -> None:
    ticket = make_verified_ticket(db_session)
    first, second = make_user(db_session), make_user(db_session)
    yesterday = datetime.now(UTC) - timedelta(days=1)
    one, _ = bought(db_session, ticket, first, completed_at=yesterday)
    two, _ = bought(db_session, ticket, second)  # listed again by its new owner, sold on

    assert ticket.current_owner_id == second.id
    assert found(db_session, {ticket.id, one.id, two.id}) == []


def two_open_listings(db: Session) -> uuid.UUID:
    db.execute(text("DROP INDEX uq_listings_one_open_per_ticket"))
    ticket = make_verified_ticket(db)
    make_listing(db, ticket=ticket, status="ACTIVE")
    make_listing(db, ticket=ticket, status="ACTIVE")
    return ticket.id


def two_live_reservations(db: Session) -> uuid.UUID:
    db.execute(text("DROP INDEX uq_reservations_one_live_per_listing"))
    listing = make_listing(db, status="RESERVED")
    make_reservation(db, listing=listing, status="CREATED")
    make_reservation(db, listing=listing, status="CREATED")
    return listing.id


def paid_order_not_paid(db: Session) -> uuid.UUID:
    order = make_order(db, status="PAYMENT_PENDING")
    make_payment(db, order=order, status="SUCCEEDED")  # the webhook's change never landed
    return order.id


def owner_is_not_buyer(db: Session) -> uuid.UUID:
    ticket = make_verified_ticket(db)
    seller = ticket.current_owner_id
    bought(db, ticket, make_user(db))
    ticket.current_owner_id = seller  # completed, but the ticket never moved
    db.flush()
    return ticket.id


def sold_twice(db: Session) -> uuid.UUID:
    ticket = make_verified_ticket(db)
    first, second = make_user(db), make_user(db)
    listing, _ = bought(db, ticket, first)
    late = make_reservation(db, listing=listing, status="FAILED", buyer_id=second.id)
    make_order(db, reservation=late, status="PAID")  # a second buyer paid for it too
    return listing.id


PLANTED: dict[str, Callable[[Session], uuid.UUID]] = {
    "two-open-listings": two_open_listings,
    "two-live-reservations": two_live_reservations,
    "paid-order-not-paid": paid_order_not_paid,
    "owner-is-not-buyer": owner_is_not_buyer,
    "sold-twice": sold_twice,
}


@pytest.mark.parametrize("invariant", PLANTED)
def test_each_invariant_catches_its_violation(db_session: Session, invariant: str) -> None:
    subject = PLANTED[invariant](db_session)

    assert found(db_session, {subject}) == [(invariant, str(subject))]


def test_a_violation_says_what_is_wrong(db_session: Session) -> None:
    subject = two_open_listings(db_session)
    (row,) = [row for row in violations(db_session.connection()) if row.subject == str(subject)]
    assert isinstance(row, Violation)
    assert row.detail == "2 open listings"
