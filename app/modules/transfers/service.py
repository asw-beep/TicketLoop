"""Transfer logic (FR-10): from a paid order to a ticket in the buyer's name.

Two consumers, each a step that ends in a commit:

1. `start_transfer` hears `payment.succeeded`. In one transaction: claim the event, create the
   transfer (`uq_transfers_order` allows one per order), move the order to TRANSFER_PENDING,
   and emit `transfer.initiated`.
2. `perform_transfer` hears `transfer.initiated`. It commits PROCESSING, asks the provider with
   no transaction open, then settles the answer in a transaction of its own: COMPLETED with the
   order, the listing and the ticket's owner, or back to wait for a retry, or review.

Why two steps and an event between them, rather than one task that does both: if a worker died
after step 1 committed, step 1's processed_events row would make the redelivered message a
no-op, and the transfer would sit INITIATED for ever. Here step 2 has its own message, which
the outbox delivers until something acknowledges it.

And when a message is lost all the same (it ran out of retries and went to a dead-letter
queue), `resume_stalled`, run by beat, picks up what has not moved for 10 minutes: a paid order
with no transfer, or a transfer still INITIATED or PROCESSING. Both steps are safe to repeat.

Lock order, as everywhere: reservation, order, payment, transfer, listing, ticket.
"""

import uuid
from collections.abc import Mapping
from datetime import timedelta
from enum import StrEnum
from typing import Any

import structlog
from sqlalchemy import Text, cast, exists, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.database.errors import violated_constraint
from app.infrastructure.external_providers.tickets import (
    TicketProvider,
    TransferOutcomeUnknown,
    TransferRefused,
    TransferRequest,
    TransferUnavailable,
)
from app.modules.audit.service import record
from app.modules.identity.models import User, UserRole
from app.modules.listings.models import Listing, ListingStatus
from app.modules.listings.state import transition as move_listing
from app.modules.orders.models import Order, OrderStatus
from app.modules.orders.service import lock_reservation
from app.modules.orders.state import transition as move_order
from app.modules.outbox.models import OutboxEvent
from app.modules.outbox.service import Consumer, EventType, done_before, emit, first_time
from app.modules.tickets.models import Ticket
from app.modules.transfers.errors import TransferNotFoundError
from app.modules.transfers.models import (
    FINAL_TRANSFER_STATUSES,
    MAX_ATTEMPTS,
    Transfer,
    TransferStatus,
)
from app.modules.transfers.state import transition

log = structlog.get_logger(__name__)

S = TransferStatus

# Untouched this long, a paid order or an unfinished transfer has lost its message. Far longer
# than any retry chain waits between tries (a minute at most).
STALLED_AFTER = timedelta(minutes=10)


class Outcome(StrEnum):
    DONE = "DONE"  # nothing more to do for this message: completed, or handled before
    RETRY = "RETRY"  # the provider was unavailable; try again later
    REVIEW = "REVIEW"  # given up; a person has to look


class PoisonEvent(Exception):
    """The event names something that does not exist. Retrying cannot help."""


class TicketChangedHands(Exception):
    """The ticket is not the seller's any more, so it cannot be handed to the buyer."""


# ------------------------------------------------------------------ step 1


def start_transfer(db: Session, event_id: uuid.UUID, payload: Mapping[str, Any]) -> bool:
    """In the caller's transaction. False if this event was handled before or there is nothing
    to start."""
    if not first_time(db, Consumer.START_TRANSFER, event_id):
        return False
    order = _lock_order(db, _uuid(payload, "order_id"))
    if order.status != OrderStatus.PAID:
        # Settled meanwhile, or started already by resume_stalled.
        log.warning("transfer.order_not_paid", order_id=str(order.id), status=order.status)
        return False
    return _begin(db, order)


def _begin(db: Session, order: Order) -> bool:
    """Create the transfer for a locked PAID order, and announce it."""
    listing = order.listing
    transfer = Transfer(
        id=uuid.uuid4(),
        order_id=order.id,
        ticket_id=listing.ticket_id,
        from_user_id=order.seller_id,
        to_user_id=order.buyer_id,
        provider_id=listing.event.provider_id,
    )
    try:
        with db.begin_nested():
            db.add(transfer)
            db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == "uq_transfers_order":
            # The safety net: a second event about the same payment. The order lock means
            # nothing gets here today.
            log.warning("transfer.exists", order_id=str(order.id))
            return False
        raise
    details = {"transfer_id": transfer.id}
    move_order(db, order, OrderStatus.TRANSFER_PENDING, actor_id=None, details=details)
    record(
        db,
        actor_id=None,
        entity_type="transfer",
        entity_id=transfer.id,
        action="transfer.initiated",
        details={"order_id": order.id},
    )
    emit(
        db,
        aggregate_type="transfer",
        aggregate_id=transfer.id,
        event_type=EventType.TRANSFER_INITIATED,
        payload={"transfer_id": transfer.id, "order_id": order.id},
    )
    return True


# ------------------------------------------------------------------ step 2


def perform_transfer(
    sessions: sessionmaker[Session],
    provider: TicketProvider,
    event_id: uuid.UUID,
    payload: Mapping[str, Any],
) -> Outcome:
    """One attempt. Commits on its own; the caller turns RETRY into a delayed retry."""
    transfer_id = _uuid(payload, "transfer_id")
    with sessions() as db, db.begin():
        if done_before(db, Consumer.PERFORM_TRANSFER, event_id):
            return Outcome.DONE
        transfer, _ = _locked(db, transfer_id)
        if transfer.status in FINAL_TRANSFER_STATUSES:
            return Outcome.DONE
        tries = transfer.attempt_count
        if tries < MAX_ATTEMPTS:
            ticket = db.get_one(Ticket, transfer.ticket_id)
            transition(
                db,
                transfer,
                S.PROCESSING,
                values={"attempt_count": Transfer.attempt_count + 1},
            )
            attempt = transfer.attempt_count
            request = TransferRequest(
                idempotency_key=str(transfer.id),
                provider_reference=ticket.provider_reference,
                to_user_id=transfer.to_user_id,
            )

    if tries >= MAX_ATTEMPTS:
        # Every try so far, and none answered: most likely each one killed its worker. Asking
        # again would only do it again, without end.
        reason = f"gave up after {tries} tries: the last one never answered"
        return _give_up(sessions, event_id, transfer_id, reason)

    # No transaction open: the provider may be slow. If this process dies here, the message is
    # delivered again and the provider is asked again with the same key, which is safe.
    try:
        reference = provider.transfer(request)
    except TransferUnavailable as error:
        if attempt < MAX_ATTEMPTS:
            _note(sessions, transfer_id, f"try {attempt}: {error}")
            return Outcome.RETRY
        return _give_up(sessions, event_id, transfer_id, f"gave up after {attempt} tries: {error}")
    except TransferRefused as error:
        return _give_up(sessions, event_id, transfer_id, f"refused: {error}")
    except TransferOutcomeUnknown as error:
        # It may have happened. Asking again could be fine with a careful provider, but the
        # rule is: an unknown outcome is never retried blindly. A person checks.
        return _give_up(sessions, event_id, transfer_id, f"outcome unknown: {error}")
    try:
        return _complete(sessions, event_id, transfer_id, reference)
    except TicketChangedHands as error:
        return _give_up(sessions, event_id, transfer_id, str(error))


def _complete(
    sessions: sessionmaker[Session], event_id: uuid.UUID, transfer_id: uuid.UUID, reference: str
) -> Outcome:
    """The provider has done it. Everything that follows from that, in one transaction."""
    with sessions() as db, db.begin():
        if not first_time(db, Consumer.PERFORM_TRANSFER, event_id):
            return Outcome.DONE
        transfer, order = _locked(db, transfer_id)
        if transfer.status != S.PROCESSING:
            return Outcome.DONE
        listing = db.get_one(Listing, order.listing_id, populate_existing=True)
        details = {"transfer_id": transfer.id}
        transition(
            db,
            transfer,
            S.COMPLETED,
            values={"provider_transfer_reference": reference, "completed_at": func.now()},
        )
        move_order(db, order, OrderStatus.COMPLETED, actor_id=None, details=details)
        move_listing(db, listing, ListingStatus.SOLD, actor_id=None, details=details)
        # The one place a ticket changes hands, and only from the seller the transfer names.
        moved = db.execute(
            update(Ticket)
            .where(
                Ticket.id == transfer.ticket_id,
                Ticket.current_owner_id == transfer.from_user_id,
            )
            .values(current_owner_id=transfer.to_user_id)
        ).rowcount
        if moved != 1:
            # Rolls all of this back. Nothing in the app moves a ticket otherwise, so this
            # is a fault for a person, not something to retry.
            raise TicketChangedHands(f"ticket {transfer.ticket_id} is no longer the seller's")
        record(
            db,
            actor_id=None,
            entity_type="ticket",
            entity_id=transfer.ticket_id,
            action="ticket.transferred",
            details=details,
        )
        emit(
            db,
            aggregate_type="transfer",
            aggregate_id=transfer.id,
            event_type=EventType.TRANSFER_COMPLETED,
            payload={
                "transfer_id": transfer.id,
                "order_id": order.id,
                "listing_id": order.listing_id,
                "buyer_id": order.buyer_id,
                "seller_id": order.seller_id,
            },
        )
    return Outcome.DONE


def _give_up(
    sessions: sessionmaker[Session], event_id: uuid.UUID, transfer_id: uuid.UUID, reason: str
) -> Outcome:
    """REQUIRES_MANUAL_REVIEW, where it stays: admin retry and refunds are cut. The listing
    stays RESERVED and the reservation CONFIRMED, so nobody else can buy the ticket and its
    event cannot be cancelled."""
    with sessions() as db, db.begin():
        if not first_time(db, Consumer.PERFORM_TRANSFER, event_id):
            return Outcome.DONE
        transfer, order = _locked(db, transfer_id)
        if transfer.status in FINAL_TRANSFER_STATUSES:
            return Outcome.DONE
        transition(db, transfer, S.REQUIRES_MANUAL_REVIEW, values={"last_error": reason[:1000]})
        move_order(
            db,
            order,
            OrderStatus.TRANSFER_FAILED,
            actor_id=None,
            details={"transfer_id": transfer.id},
        )
        emit(
            db,
            aggregate_type="transfer",
            aggregate_id=transfer.id,
            event_type=EventType.TRANSFER_FAILED,
            payload={
                "transfer_id": transfer.id,
                "order_id": order.id,
                "listing_id": order.listing_id,
                "buyer_id": order.buyer_id,
                "seller_id": order.seller_id,
            },
        )
    log.error("transfer.needs_review", transfer_id=str(transfer_id), reason=reason)
    return Outcome.REVIEW


def _note(sessions: sessionmaker[Session], transfer_id: uuid.UUID, error: str) -> None:
    with sessions() as db, db.begin():
        db.execute(
            update(Transfer).where(Transfer.id == transfer_id).values(last_error=error[:1000])
        )
    log.warning("transfer.retry", transfer_id=str(transfer_id), error=error)


# ------------------------------------------------------------------ helpers


def _lock_order(db: Session, order_id: uuid.UUID) -> Order:
    reservation_id = db.scalar(select(Order.reservation_id).where(Order.id == order_id))
    if reservation_id is None:
        raise PoisonEvent(f"no order {order_id}")
    lock_reservation(db, reservation_id)
    return db.scalars(
        select(Order)
        .where(Order.id == order_id)
        .with_for_update(of=Order)
        .execution_options(populate_existing=True)
    ).one()


def _locked(db: Session, transfer_id: uuid.UUID) -> tuple[Transfer, Order]:
    """The transfer and its order, as they are now, and nobody else's until we commit."""
    order_id = db.scalar(select(Transfer.order_id).where(Transfer.id == transfer_id))
    if order_id is None:
        raise PoisonEvent(f"no transfer {transfer_id}")
    order = _lock_order(db, order_id)
    transfer = db.scalars(
        select(Transfer)
        .where(Transfer.id == transfer_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    return transfer, order


def _uuid(payload: Mapping[str, Any], key: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload[key]))
    except (KeyError, ValueError) as error:
        raise PoisonEvent(f"the event has no usable {key}") from error


def get_transfer_for(db: Session, transfer_id: uuid.UUID, user: User) -> Transfer:
    """The buyer, the seller and admins. Anyone else gets the same 404 as for no transfer."""
    transfer = db.get(Transfer, transfer_id)
    if transfer is None or not (
        user.id in (transfer.from_user_id, transfer.to_user_id) or user.role == UserRole.ADMIN
    ):
        raise TransferNotFoundError()
    return transfer


def transfer_of(db: Session, order_id: uuid.UUID) -> Transfer | None:
    return db.scalar(select(Transfer).where(Transfer.order_id == order_id))


# ------------------------------------------------------------------ the sweep


def resume_stalled(
    db: Session, *, stalled_for: timedelta = STALLED_AFTER, limit: int = 100
) -> tuple[int, int]:
    """In the caller's transaction. Starts the transfer of every PAID order left without one,
    and announces again every transfer left INITIATED or PROCESSING, when nothing has touched
    it for `stalled_for` and no event about it is still waiting in the outbox (then the relay
    will deliver it, and more would only pile up). Returns (started, resumed)."""
    cutoff = func.now() - stalled_for
    waiting = select(OutboxEvent.id).where(OutboxEvent.published_at.is_(None))

    orphans = db.scalars(
        select(Order.id)
        .where(
            Order.status == OrderStatus.PAID,
            Order.updated_at < cutoff,
            ~exists().where(Transfer.order_id == Order.id),
            ~exists(
                waiting.where(
                    OutboxEvent.event_type == EventType.PAYMENT_SUCCEEDED,
                    OutboxEvent.payload["order_id"].astext == cast(Order.id, Text),
                )
            ),
        )
        .order_by(Order.updated_at)
        .limit(limit)
    ).all()
    started = 0
    for order_id in orphans:
        order = _lock_order(db, order_id)  # waits for anyone settling it right now
        if order.status == OrderStatus.PAID and transfer_of(db, order.id) is None:
            started += _begin(db, order)
            log.warning("transfer.started_by_sweep", order_id=str(order.id))

    # SKIP LOCKED: a transfer locked right now is being worked on. And it never waits, so taking
    # a transfer before its order here cannot deadlock.
    stalled = db.scalars(
        select(Transfer)
        .where(
            Transfer.status.in_((S.INITIATED, S.PROCESSING)),
            Transfer.updated_at < cutoff,
            ~exists(waiting.where(OutboxEvent.aggregate_id == Transfer.id)),
        )
        .order_by(Transfer.updated_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for transfer in stalled:
        transfer.updated_at = func.now()  # so the next run leaves it alone for a while
        record(
            db,
            actor_id=None,
            entity_type="transfer",
            entity_id=transfer.id,
            action="transfer.resumed",
            details={"status": transfer.status, "attempt_count": transfer.attempt_count},
        )
        emit(
            db,
            aggregate_type="transfer",
            aggregate_id=transfer.id,
            event_type=EventType.TRANSFER_INITIATED,
            payload={"transfer_id": transfer.id, "order_id": transfer.order_id},
        )
        log.warning("transfer.resumed", transfer_id=str(transfer.id), status=transfer.status)
    return started, len(stalled)
