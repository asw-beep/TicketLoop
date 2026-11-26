"""Transfers: a paid order ends with the ticket in the buyer's name, once, or in review."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import create_access_token
from app.infrastructure.external_providers.tickets import FakeTicketProvider
from app.modules.audit.models import AuditLog
from app.modules.events.models import Event
from app.modules.listings.errors import EventHasReservationsError
from app.modules.listings.models import Listing
from app.modules.listings.service import cancel_open_listings_for_event
from app.modules.orders.models import Order
from app.modules.outbox.models import OutboxEvent
from app.modules.tickets.models import Ticket
from app.modules.transfers.models import MAX_ATTEMPTS, Transfer
from app.modules.transfers.service import (
    Outcome,
    PoisonEvent,
    perform_transfer,
    resume_stalled,
    start_transfer,
)
from tests.integration.api import bearer, problem_code
from tests.integration.factories import make_document, make_user
from tests.integration.messaging import events, last_message, paid, sessions  # noqa: F401

pytestmark = pytest.mark.integration

Sessions = sessionmaker[Session]


def start(sessions: Sessions, msg: dict[str, Any]) -> bool:  # noqa: F811
    with sessions() as db, db.begin():
        return start_transfer(db, uuid.UUID(msg["id"]), msg["payload"])


def started(sessions: Sessions) -> tuple[uuid.UUID, dict[str, Any]]:  # noqa: F811
    """A paid order whose transfer has started: its id, and the transfer.initiated message."""
    order_id, msg = paid(sessions)
    assert start(sessions, msg)
    with sessions() as db:
        return order_id, last_message(db, "transfer.initiated")


def perform(sessions: Sessions, provider: FakeTicketProvider, msg: dict[str, Any]) -> Outcome:  # noqa: F811
    return perform_transfer(sessions, provider, uuid.UUID(msg["id"]), msg["payload"])


def state(sessions: Sessions, order_id: uuid.UUID) -> dict[str, Any]:  # noqa: F811
    with sessions() as db:
        order = db.get_one(Order, order_id)
        transfer = db.scalars(select(Transfer).where(Transfer.order_id == order_id)).all()
        listing = db.get_one(Listing, order.listing_id)
        ticket = db.get_one(Ticket, listing.ticket_id)
        return {
            "order": order.status,
            "transfers": [t.status for t in transfer],
            "attempts": [t.attempt_count for t in transfer],
            "listing": listing.status,
            "owner": "buyer" if ticket.current_owner_id == order.buyer_id else "seller",
            "transfer": transfer[0] if transfer else None,
        }


# ---------------------------------------------------------------- step 1: payment.succeeded


def test_a_paid_order_gets_one_transfer_waiting_for_the_provider(sessions: Sessions) -> None:  # noqa: F811
    order_id, msg = paid(sessions)

    assert start(sessions, msg)

    now = state(sessions, order_id)
    assert (now["order"], now["transfers"], now["listing"], now["owner"]) == (
        "TRANSFER_PENDING",
        ["INITIATED"],
        "RESERVED",
        "seller",
    )
    with sessions() as db:
        (initiated,) = events(db, "transfer.initiated")
        assert initiated.payload == {
            "transfer_id": str(now["transfer"].id),
            "order_id": str(order_id),
        }
        actions = db.scalars(
            select(AuditLog.action).where(AuditLog.entity_id == now["transfer"].id)
        ).all()
        assert actions == ["transfer.initiated"]


def test_the_same_event_delivered_twice_starts_one_transfer(sessions: Sessions) -> None:  # noqa: F811
    order_id, msg = paid(sessions)

    assert start(sessions, msg)
    assert not start(sessions, msg)

    assert state(sessions, order_id)["transfers"] == ["INITIATED"]
    with sessions() as db:
        assert len(events(db, "transfer.initiated")) == 1


def test_another_event_about_the_same_order_starts_nothing(sessions: Sessions) -> None:  # noqa: F811
    order_id, msg = paid(sessions)
    assert start(sessions, msg)

    assert not start(sessions, msg | {"id": str(uuid.uuid4())})

    assert state(sessions, order_id)["transfers"] == ["INITIATED"]


@pytest.mark.parametrize("payload", [{}, {"order_id": "nonsense"}, {"order_id": str(uuid.uuid4())}])
def test_an_event_about_nothing_is_poison(sessions: Sessions, payload: dict[str, Any]) -> None:  # noqa: F811
    with pytest.raises(PoisonEvent):
        start(sessions, {"id": str(uuid.uuid4()), "payload": payload})


# ---------------------------------------------------------------- step 2: transfer.initiated


def test_success_moves_the_ticket_and_everything_with_it(sessions: Sessions) -> None:  # noqa: F811
    order_id, msg = started(sessions)
    provider = FakeTicketProvider()

    assert perform(sessions, provider, msg) == Outcome.DONE

    now = state(sessions, order_id)
    assert (now["order"], now["transfers"], now["listing"], now["owner"]) == (
        "COMPLETED",
        ["COMPLETED"],
        "SOLD",
        "buyer",
    )
    transfer = now["transfer"]
    assert transfer.attempt_count == 1
    assert transfer.completed_at is not None
    assert transfer.provider_transfer_reference == f"fpv_{transfer.id.hex}"
    with sessions() as db:
        (completed,) = events(db, "transfer.completed")
        order = db.get_one(Order, order_id)
        assert completed.payload == {
            "transfer_id": str(transfer.id),
            "order_id": str(order_id),
            "listing_id": str(order.listing_id),
            "buyer_id": str(order.buyer_id),
            "seller_id": str(order.seller_id),
        }
        ticket_id = db.get_one(Listing, order.listing_id).ticket_id
        actions = db.scalars(select(AuditLog.action).where(AuditLog.entity_id == ticket_id)).all()
        assert actions == ["ticket.transferred"]
        listing_actions = db.scalars(
            select(AuditLog.action).where(AuditLog.entity_id == order.listing_id)
        ).all()
        assert listing_actions[-1] == "listing.sold"


def test_a_message_delivered_again_after_success_does_nothing(sessions: Sessions) -> None:  # noqa: F811
    order_id, msg = started(sessions)
    provider = FakeTicketProvider()
    assert perform(sessions, provider, msg) == Outcome.DONE

    assert perform(sessions, provider, msg) == Outcome.DONE

    assert sum(provider.calls.values()) == 1
    assert state(sessions, order_id)["attempts"] == [1]
    with sessions() as db:
        assert len(events(db, "transfer.completed")) == 1


def test_a_worker_that_died_mid_call_asks_again_with_the_same_key(sessions: Sessions) -> None:  # noqa: F811
    order_id, msg = started(sessions)
    transfer_id = uuid.UUID(msg["payload"]["transfer_id"])
    # As a worker leaves it when it dies after committing PROCESSING, before the answer.
    with sessions() as db, db.begin():
        db.execute(
            update(Transfer)
            .where(Transfer.id == transfer_id)
            .values(status="PROCESSING", attempt_count=1)
        )
    provider = FakeTicketProvider()

    assert perform(sessions, provider, msg) == Outcome.DONE

    assert list(provider.calls) == [str(transfer_id)]
    now = state(sessions, order_id)
    assert (now["order"], now["owner"], now["attempts"]) == ("COMPLETED", "buyer", [2])


def test_a_provider_that_is_busy_is_asked_again_later(sessions: Sessions) -> None:  # noqa: F811
    order_id, msg = started(sessions)
    provider = FakeTicketProvider("retryable", failures=2)

    assert perform(sessions, provider, msg) == Outcome.RETRY
    assert state(sessions, order_id)["transfer"].last_error == "try 1: provider busy (try 1)"
    assert perform(sessions, provider, msg) == Outcome.RETRY
    assert perform(sessions, provider, msg) == Outcome.DONE

    now = state(sessions, order_id)
    assert (now["order"], now["transfers"], now["attempts"]) == ("COMPLETED", ["COMPLETED"], [3])


def test_five_busy_answers_end_in_review(sessions: Sessions) -> None:  # noqa: F811
    order_id, msg = started(sessions)
    provider = FakeTicketProvider("retryable")

    outcomes = [perform(sessions, provider, msg) for _ in range(MAX_ATTEMPTS)]

    assert outcomes == [Outcome.RETRY] * (MAX_ATTEMPTS - 1) + [Outcome.REVIEW]
    now = state(sessions, order_id)
    assert (now["order"], now["transfers"], now["listing"], now["owner"]) == (
        "TRANSFER_FAILED",
        ["REQUIRES_MANUAL_REVIEW"],
        "RESERVED",
        "seller",
    )
    assert now["transfer"].last_error.startswith("gave up after 5 tries")
    # And there it stays: another delivery asks nobody anything.
    assert perform(sessions, provider, msg) == Outcome.DONE
    assert sum(provider.calls.values()) == MAX_ATTEMPTS


def test_a_transfer_that_has_had_its_tries_goes_to_review_without_asking_again(
    sessions: Sessions,  # noqa: F811
) -> None:
    # Review finding: a message that kills its worker during the call is delivered again and
    # again, and each delivery asked the provider once more, without end.
    order_id, msg = started(sessions)
    with sessions() as db, db.begin():
        db.execute(
            update(Transfer)
            .where(Transfer.id == uuid.UUID(msg["payload"]["transfer_id"]))
            .values(status="PROCESSING", attempt_count=MAX_ATTEMPTS)
        )
    provider = FakeTicketProvider()

    assert perform(sessions, provider, msg) == Outcome.REVIEW

    assert not provider.calls
    now = state(sessions, order_id)
    assert (now["order"], now["transfers"], now["owner"]) == (
        "TRANSFER_FAILED",
        ["REQUIRES_MANUAL_REVIEW"],
        "seller",
    )
    assert now["transfer"].last_error.startswith("gave up after 5 tries")


@pytest.mark.parametrize("mode", ["permanent", "unknown"])
def test_a_refusal_or_an_unknown_outcome_ends_in_review_at_once(
    sessions: Sessions,  # noqa: F811
    mode: Any,
) -> None:
    order_id, msg = started(sessions)
    provider = FakeTicketProvider(mode)

    assert perform(sessions, provider, msg) == Outcome.REVIEW

    assert sum(provider.calls.values()) == 1
    now = state(sessions, order_id)
    assert (now["order"], now["transfers"], now["owner"]) == (
        "TRANSFER_FAILED",
        ["REQUIRES_MANUAL_REVIEW"],
        "seller",
    )
    with sessions() as db:
        (failed,) = events(db, "transfer.failed")
        assert failed.payload["order_id"] == str(order_id)


def test_a_ticket_that_changed_hands_meanwhile_goes_to_review(sessions: Sessions) -> None:  # noqa: F811
    order_id, msg = started(sessions)
    with sessions() as db, db.begin():
        order = db.get_one(Order, order_id)
        ticket_id = db.get_one(Listing, order.listing_id).ticket_id
        db.execute(
            update(Ticket).where(Ticket.id == ticket_id).values(current_owner_id=order.buyer_id)
        )

    assert perform(sessions, FakeTicketProvider(), msg) == Outcome.REVIEW

    now = state(sessions, order_id)
    assert (now["order"], now["listing"]) == ("TRANSFER_FAILED", "RESERVED")
    assert "no longer the seller's" in now["transfer"].last_error


def test_an_event_cannot_be_cancelled_while_its_ticket_awaits_review(
    sessions: Sessions,  # noqa: F811
) -> None:
    order_id, msg = started(sessions)
    perform(sessions, FakeTicketProvider("permanent"), msg)

    with sessions() as db, db.begin():
        order = db.get_one(Order, order_id)
        event = db.get_one(Event, db.get_one(Listing, order.listing_id).event_id)
        with pytest.raises(EventHasReservationsError):
            cancel_open_listings_for_event(db, event.id, actor_id=make_user(db).id)


# ---------------------------------------------------------------- what buyer and seller see


def as_user(db_app: FastAPI, user_id: uuid.UUID) -> dict[str, str]:
    return bearer(create_access_token(user_id, db_app.state.settings))


def test_the_order_shows_how_far_the_ticket_has_got(
    sessions: Sessions,  # noqa: F811
    api: TestClient,
    db_app: FastAPI,
) -> None:
    order_id, msg = started(sessions)
    with sessions() as db:
        buyer_id = db.get_one(Order, order_id).buyer_id
    url = f"/api/v1/orders/{order_id}"

    pending = api.get(url, headers=as_user(db_app, buyer_id)).json()
    perform(sessions, FakeTicketProvider(), msg)
    done = api.get(url, headers=as_user(db_app, buyer_id)).json()

    assert (pending["status"], pending["transfer"]["status"]) == ("TRANSFER_PENDING", "INITIATED")
    assert set(pending["transfer"]) == {
        "id",
        "order_id",
        "status",
        "attempt_count",
        "initiated_at",
        "completed_at",
    }
    assert (done["status"], done["transfer"]["status"]) == ("COMPLETED", "COMPLETED")
    assert done["transfer"]["completed_at"] is not None


def test_why_a_transfer_failed_is_for_admins_only(
    sessions: Sessions,  # noqa: F811
    api: TestClient,
    db_app: FastAPI,
) -> None:
    order_id, msg = started(sessions)
    perform(sessions, FakeTicketProvider("permanent"), msg)
    with sessions() as db, db.begin():
        order = db.get_one(Order, order_id)
        transfer = state(sessions, order_id)["transfer"]
        admin = make_user(db, role="ADMIN")
        people = {"buyer": order.buyer_id, "seller": order.seller_id, "admin": admin.id}
        stranger = make_user(db).id
    url = f"/api/v1/transfers/{transfer.id}"

    seen = {who: api.get(url, headers=as_user(db_app, uid)) for who, uid in people.items()}

    assert {who: r.status_code for who, r in seen.items()} == {
        "buyer": 200,
        "seller": 200,
        "admin": 200,
    }
    assert seen["buyer"].json()["status"] == "REQUIRES_MANUAL_REVIEW"
    assert seen["buyer"].json()["last_error"] is None
    assert seen["seller"].json()["last_error"] is None
    assert seen["admin"].json()["last_error"]
    for path in (url, f"/api/v1/transfers/{uuid.uuid4()}"):
        response = api.get(path, headers=as_user(db_app, stranger))
        assert response.status_code == 404
        assert problem_code(response) == "transfer-not-found"


def test_after_the_sale_the_sellers_evidence_stays_with_the_seller(
    sessions: Sessions,  # noqa: F811
    api: TestClient,
    db_app: FastAPI,
) -> None:
    # The buyer owns the ticket now. What the seller showed to get it verified, the files and
    # where they bought it, is still the seller's, and the admins'.
    order_id, msg = started(sessions)
    perform(sessions, FakeTicketProvider(), msg)
    with sessions() as db, db.begin():
        order = db.get_one(Order, order_id)
        ticket = db.get_one(Ticket, db.get_one(Listing, order.listing_id).ticket_id)
        document = make_document(db, ticket_id=ticket.id, uploaded_by=order.seller_id)
        buyer, seller = as_user(db_app, order.buyer_id), as_user(db_app, order.seller_id)
    url = f"/api/v1/tickets/{ticket.id}"

    owned = api.get(url, headers=buyer).json()
    mine = api.get("/api/v1/tickets/me", headers=buyer).json()["items"]

    assert owned["current_owner_id"] == str(order.buyer_id)
    assert owned["purchase_reference"] is None
    assert [(t["id"], t["purchase_reference"]) for t in mine] == [(str(ticket.id), None)]
    assert api.get(f"{url}/documents", headers=buyer).json() == []
    link = api.get(f"{url}/documents/{document.id}", headers=buyer)
    assert (link.status_code, problem_code(link)) == (404, "document-not-found")

    sold = api.get(url, headers=seller)
    assert sold.status_code == 200
    assert sold.json()["purchase_reference"] == ticket.purchase_reference
    assert [d["id"] for d in api.get(f"{url}/documents", headers=seller).json()] == [
        str(document.id)
    ]
    assert api.get("/api/v1/tickets/me", headers=seller).json()["items"] == []


# ---------------------------------------------------------------- the sweep
# Review finding: a message that ended in a dead-letter queue (a database restart longer than
# its retries, say) left the order PAID, or the transfer INITIATED, for ever, while the order
# page said the ticket was on its way.


def published(sessions: Sessions) -> None:  # noqa: F811
    """As if every event so far went out, and its message was then lost."""
    with sessions() as db, db.begin():
        db.execute(update(OutboxEvent).values(published_at=func.now()))


def untouched_for(sessions: Sessions, model: type[Order] | type[Transfer], minutes: int) -> None:  # noqa: F811
    with sessions() as db, db.begin():
        db.execute(update(model).values(updated_at=func.now() - timedelta(minutes=minutes)))


def resume(sessions: Sessions) -> tuple[int, int]:  # noqa: F811
    with sessions() as db, db.begin():
        return resume_stalled(db)


def test_a_paid_order_whose_message_was_lost_gets_its_transfer(sessions: Sessions) -> None:  # noqa: F811
    order_id, msg = paid(sessions)
    published(sessions)
    untouched_for(sessions, Order, 11)

    assert resume(sessions) == (1, 0)

    now = state(sessions, order_id)
    assert (now["order"], now["transfers"]) == ("TRANSFER_PENDING", ["INITIATED"])
    with sessions() as db:
        assert len(events(db, "transfer.initiated")) == 1
    # The lost message turning up after all starts nothing more.
    assert not start(sessions, msg)
    assert state(sessions, order_id)["transfers"] == ["INITIATED"]


def test_a_transfer_whose_message_was_lost_is_carried_out(sessions: Sessions) -> None:  # noqa: F811
    order_id, _ = started(sessions)
    published(sessions)
    untouched_for(sessions, Transfer, 11)

    assert resume(sessions) == (0, 1)
    assert resume(sessions) == (0, 0)  # not again on the next run: it was just touched

    with sessions() as db:
        assert len(events(db, "transfer.initiated")) == 2
        again = last_message(db, "transfer.initiated")
    assert perform(sessions, FakeTicketProvider(), again) == Outcome.DONE
    now = state(sessions, order_id)
    assert (now["order"], now["owner"]) == ("COMPLETED", "buyer")


def test_a_transfer_stuck_mid_call_is_asked_about_again(sessions: Sessions) -> None:  # noqa: F811
    order_id, msg = started(sessions)
    with sessions() as db, db.begin():
        db.execute(
            update(Transfer)
            .where(Transfer.id == uuid.UUID(msg["payload"]["transfer_id"]))
            .values(status="PROCESSING", attempt_count=1)
        )
    published(sessions)
    untouched_for(sessions, Transfer, 11)

    assert resume(sessions) == (0, 1)


def test_nothing_is_resumed_while_its_event_still_waits_to_be_published(
    sessions: Sessions,  # noqa: F811
) -> None:
    # The broker has been down for a while: the outbox will deliver these, so the sweep must
    # not pile more events on top.
    paid(sessions)
    started(sessions)
    untouched_for(sessions, Order, 30)
    untouched_for(sessions, Transfer, 30)

    assert resume(sessions) == (0, 0)


def test_recent_and_finished_work_is_left_alone(sessions: Sessions) -> None:  # noqa: F811
    paid(sessions)
    started(sessions)
    published(sessions)
    order_id, msg = started(sessions)
    assert perform(sessions, FakeTicketProvider(), msg) == Outcome.DONE
    published(sessions)
    # Only the finished one is old; the other two were touched a moment ago.
    with sessions() as db, db.begin():
        db.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(updated_at=func.now() - timedelta(minutes=30))
        )
        db.execute(
            update(Transfer)
            .where(Transfer.order_id == order_id)
            .values(updated_at=func.now() - timedelta(minutes=30))
        )

    assert resume(sessions) == (0, 0)
