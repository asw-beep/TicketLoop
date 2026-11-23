"""A duplicate refused by a unique constraint leaves the request's session usable.

Create, register and provider-create flush the new row inside a SAVEPOINT, so a duplicate rolls
back only that savepoint: everything else the request did is still there, and the session can
carry on. The row must be added inside the savepoint block. `begin_nested()` flushes whatever is
already pending before it issues SAVEPOINT, so a row added before the block is inserted outside
the savepoint, and its failure takes the whole transaction down with it.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.events.errors import ProviderCodeTakenError
from app.modules.events.models import TicketProvider
from app.modules.events.schemas import ProviderIn
from app.modules.events.service import create_provider
from app.modules.identity.models import User, UserRole
from app.modules.listings.errors import ListingAlreadyOpenError
from app.modules.listings.models import Listing
from app.modules.listings.schemas import ListingIn
from app.modules.listings.service import create_listing
from app.modules.tickets.errors import TicketAlreadyRegisteredError
from app.modules.tickets.models import Ticket
from app.modules.tickets.schemas import TicketIn
from app.modules.tickets.service import register_ticket
from tests.integration.factories import (
    make_event,
    make_listing,
    make_provider,
    make_user,
    make_verified_ticket,
)

pytestmark = pytest.mark.integration


def count(db: Session, model: type, *where: object) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where))


def test_a_second_open_listing_is_refused_and_the_session_carries_on(db_session: Session):
    seller = make_user(db_session)
    event = make_event(db_session, status="PUBLISHED")
    ticket = make_verified_ticket(
        db_session, event_id=event.id, original_owner_id=seller.id, current_owner_id=seller.id
    )
    make_listing(db_session, ticket=ticket, status="DRAFT")

    with pytest.raises(ListingAlreadyOpenError):
        create_listing(
            db_session, seller, ListingIn(ticket_id=ticket.id, asking_price_paise=200_000)
        )

    assert count(db_session, Listing, Listing.ticket_id == ticket.id) == 1
    assert count(db_session, User, User.id == seller.id) == 1


def test_a_second_registration_is_refused_and_the_session_carries_on(db_session: Session):
    owner = make_user(db_session)
    event = make_event(db_session, status="PUBLISHED")
    data = TicketIn(
        event_id=event.id,
        ticket_category="GENERAL",
        original_price_paise=250_000,
        purchase_reference="ORD-SAVEPOINT",
        provider_reference="TKT-SAVEPOINT",
    )
    register_ticket(db_session, owner, data)

    with pytest.raises(TicketAlreadyRegisteredError):
        register_ticket(db_session, owner, data)

    assert count(db_session, Ticket, Ticket.event_id == event.id) == 1


def test_a_taken_provider_code_is_refused_and_the_session_carries_on(db_session: Session):
    admin = make_user(db_session, role=UserRole.ADMIN)
    taken = make_provider(db_session)

    with pytest.raises(ProviderCodeTakenError):
        create_provider(db_session, ProviderIn(code=taken.code, name="Again"), admin)

    assert count(db_session, TicketProvider, TicketProvider.code == taken.code) == 1
    assert count(db_session, User, User.id == admin.id) == 1
