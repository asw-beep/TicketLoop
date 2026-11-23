"""Ticket logic (FR-03). Functions take the request's Session and never commit.

Who may see a ticket is decided in exactly one place, get_visible_ticket(). Every route that
takes a ticket ID goes through it, so there is one place to get IDOR right: the owner and
admins see the ticket, everyone else gets the same 404 as for an ID that does not exist.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.pagination import paginate
from app.infrastructure.database.errors import violated_constraint
from app.modules.audit.service import record
from app.modules.events.errors import EventNotFoundError
from app.modules.events.models import Event, EventStatus
from app.modules.identity.models import User, UserRole
from app.modules.tickets.errors import (
    EventNotOpenError,
    EventStartedError,
    OwnTicketReviewError,
    TicketAlreadyRegisteredError,
    TicketNotFoundError,
    TicketNotPendingError,
)
from app.modules.tickets.models import Ticket, VerificationStatus
from app.modules.tickets.schemas import TicketIn, VerifyIn


def get_visible_ticket(db: Session, ticket_id: uuid.UUID, user: User) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise TicketNotFoundError()
    if ticket.current_owner_id != user.id and user.role != UserRole.ADMIN:
        raise TicketNotFoundError()  # not yours: indistinguishable from not there
    return ticket


def register_ticket(db: Session, owner: User, data: TicketIn) -> Ticket:
    event = db.get(Event, data.event_id)
    if event is None or event.status == EventStatus.DRAFT:
        raise EventNotFoundError()  # users cannot see drafts
    if event.status != EventStatus.PUBLISHED:
        raise EventNotOpenError(f"The event is {event.status}.")
    if event.starts_at <= datetime.now(UTC):
        raise EventStartedError()

    ticket = Ticket(
        **data.model_dump(),
        original_owner_id=owner.id,
        current_owner_id=owner.id,
    )
    try:
        # The unique constraint decides duplicates, not a check-then-insert: two people
        # registering the same ticket at the same instant cannot both succeed. The row is
        # added inside the SAVEPOINT (begin_nested flushes anything already pending before
        # it starts), so a duplicate rolls back only the savepoint.
        with db.begin_nested():
            db.add(ticket)
            db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == "uq_tickets_event_provider_reference":
            raise TicketAlreadyRegisteredError() from None
        raise
    db.refresh(ticket)
    return ticket


def list_my_tickets(
    db: Session, owner: User, cursor: str | None, limit: int
) -> tuple[list[Ticket], str | None]:
    stmt = (
        select(Ticket)
        .where(Ticket.current_owner_id == owner.id)
        .order_by(Ticket.created_at.desc(), Ticket.id.desc())
    )
    return paginate(db, stmt, cursor, limit)


def list_pending_tickets(
    db: Session, cursor: str | None, limit: int
) -> tuple[list[Ticket], str | None]:
    """The review queue, oldest first."""
    stmt = (
        select(Ticket)
        .where(Ticket.verification_status == VerificationStatus.PENDING)
        .order_by(Ticket.created_at, Ticket.id)
    )
    return paginate(db, stmt, cursor, limit)


def verify_ticket(db: Session, ticket_id: uuid.UUID, admin: User, data: VerifyIn) -> Ticket:
    """Record an admin's decision. It happens once: the UPDATE applies only while the ticket
    is still PENDING, so two admins deciding at the same instant cannot both succeed.

    A rejected ticket is never transfer-eligible, whatever the request says. No admin
    verifies a ticket they own.
    """
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise TicketNotFoundError()
    if ticket.current_owner_id == admin.id:
        raise OwnTicketReviewError()
    decided = db.execute(
        update(Ticket)
        .where(Ticket.id == ticket_id, Ticket.verification_status == VerificationStatus.PENDING)
        .values(
            verification_status=data.decision,
            transfer_eligible=data.transfer_eligible and data.decision == "VERIFIED",
            verification_note=data.note,
            verified_by=admin.id,
            verified_at=func.now(),
            updated_at=func.now(),
        )
        .returning(Ticket.id)
    ).scalar_one_or_none()
    db.refresh(ticket)
    if decided is None:
        raise TicketNotPendingError(f"The ticket is already {ticket.verification_status}.")
    record(
        db,
        actor_id=admin.id,
        entity_type="ticket",
        entity_id=ticket.id,
        action="ticket.verified" if data.decision == "VERIFIED" else "ticket.rejected",
        details={"transfer_eligible": ticket.transfer_eligible, "note": ticket.verification_note},
    )
    return ticket
