"""The outbox: an event exists exactly when its change does, and the relay publishes each one,
waiting and retrying when the broker does not confirm."""

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.database.session import create_session_factory
from app.modules.outbox.models import OutboxEvent
from app.modules.outbox.relay import backoff, relay_once
from app.modules.outbox.service import Consumer, EventType, emit
from app.modules.payments import service as payments
from app.modules.reservations.service import expire_reservations
from tests.integration.committed import committed_rows  # noqa: F401
from tests.integration.factories import make_payment, make_reservation
from tests.integration.messaging import RecordingPublisher, events, sessions  # noqa: F401

pytestmark = pytest.mark.integration

Sessions = sessionmaker[Session]


def emit_some(
    sessions: Sessions,  # noqa: F811
    count: int,
    event_type: EventType = EventType.PAYMENT_FAILED,
) -> list[uuid.UUID]:
    with sessions() as db, db.begin():
        return [
            emit(
                db,
                aggregate_type="order",
                aggregate_id=uuid.uuid4(),
                event_type=event_type,
                payload={"n": n},
            )
            for n in range(count)
        ]


# ---------------------------------------------------------------- writing events


def test_an_event_rolls_back_with_its_change(db_session: Session) -> None:
    with pytest.raises(RuntimeError), db_session.begin_nested():
        emit(
            db_session,
            aggregate_type="order",
            aggregate_id=uuid.uuid4(),
            event_type=EventType.PAYMENT_FAILED,
            payload={},
        )
        raise RuntimeError("the change failed")

    assert events(db_session) == []


def test_a_payment_that_succeeds_announces_it_once(db_session: Session) -> None:
    payment = make_payment(db_session, status="PENDING")
    order = payment.order

    payments.succeed(db_session, payment.id, source="test", snapshot={})
    payments.succeed(db_session, payment.id, source="test", snapshot={})

    (event,) = events(db_session)
    assert (event.event_type, event.aggregate_type, event.aggregate_id) == (
        "payment.succeeded",
        "order",
        order.id,
    )
    assert event.payload == {
        "order_id": str(order.id),
        "payment_id": str(payment.id),
        "reservation_id": str(order.reservation_id),
        "listing_id": str(order.listing_id),
        "buyer_id": str(order.buyer_id),
        "seller_id": str(order.seller_id),
        "amount_paise": order.amount_paise,
    }
    assert event.published_at is None


@pytest.mark.parametrize(("timed_out", "reason"), [(False, "declined"), (True, "timeout")])
def test_a_payment_that_fails_announces_it_with_the_reason(
    db_session: Session, timed_out: bool, reason: str
) -> None:
    payment = make_payment(db_session, status="PENDING")

    payments.fail(db_session, payment.id, reason=reason, source="test", timed_out=timed_out)

    (event,) = events(db_session)
    assert event.event_type == "payment.failed"
    assert event.payload["reason"] == reason
    assert event.payload["buyer_id"] == str(payment.order.buyer_id)


def test_a_hold_that_runs_out_announces_it(db_session: Session) -> None:
    hold = make_reservation(db_session, expires_at=datetime.now(UTC) - timedelta(seconds=1))

    expire_reservations(db_session)

    (event,) = events(db_session)
    assert (event.event_type, event.aggregate_id) == ("reservation.expired", hold.id)
    assert event.payload == {
        "reservation_id": str(hold.id),
        "listing_id": str(hold.listing_id),
        "buyer_id": str(hold.buyer_id),
        "seller_id": str(hold.listing.seller_id),
    }


# ---------------------------------------------------------------- the relay


def test_each_event_goes_to_each_of_its_consumers_and_is_marked(sessions: Sessions) -> None:  # noqa: F811
    with sessions() as db, db.begin():
        first = emit(
            db,
            aggregate_type="order",
            aggregate_id=uuid.uuid4(),
            event_type=EventType.PAYMENT_SUCCEEDED,
            payload={},
        )
        second = emit(
            db,
            aggregate_type="order",
            aggregate_id=uuid.uuid4(),
            event_type=EventType.PAYMENT_FAILED,
            payload={},
        )
    publisher = RecordingPublisher()

    report = relay_once(sessions, publisher)

    assert (report.claimed, report.published, report.failed) == (2, 2, 0)
    assert publisher.ids() == [
        ("transfers.start", first),
        ("notifications.notify", first),
        ("notifications.notify", second),
    ]
    _, msg = publisher.sent[0]
    assert set(msg) == {"id", "type", "aggregate_type", "aggregate_id", "payload", "headers"}
    with sessions() as db:
        assert all(event.published_at is not None for event in events(db))
    # A second pass finds nothing.
    assert relay_once(sessions, RecordingPublisher()).claimed == 0


def test_an_unconfirmed_event_waits_and_the_rest_of_the_batch_is_untouched(
    sessions: Sessions,  # noqa: F811
) -> None:
    first, *rest = emit_some(sessions, 3)

    report = relay_once(sessions, RecordingPublisher(fail_on="*"))

    assert (report.claimed, report.published, report.failed) == (3, 0, 1)
    assert report.error == "broker unreachable"
    with sessions() as db:
        failed = db.get_one(OutboxEvent, first)
        assert (failed.published_at, failed.retry_count, failed.last_error) == (
            None,
            1,
            "broker unreachable",
        )
        assert failed.next_attempt_at > db.scalar(select(func.now()))
        for event_id in rest:
            untouched = db.get_one(OutboxEvent, event_id)
            assert (untouched.retry_count, untouched.published_at) == (0, None)


def test_a_waiting_event_is_left_alone_until_it_is_due(sessions: Sessions) -> None:  # noqa: F811
    (event_id,) = emit_some(sessions, 1)
    relay_once(sessions, RecordingPublisher(fail_on="*"))

    publisher = RecordingPublisher()
    assert relay_once(sessions, publisher).claimed == 0

    with sessions() as db, db.begin():
        db.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .values(next_attempt_at=func.now() - timedelta(seconds=1))
        )
    assert relay_once(sessions, publisher).published == 1
    assert publisher.ids() == [("notifications.notify", event_id)]


def test_an_event_half_published_is_sent_again_whole(sessions: Sessions) -> None:  # noqa: F811
    (event_id,) = emit_some(sessions, 1, EventType.PAYMENT_SUCCEEDED)
    flaky = RecordingPublisher(fail_on="notifications.notify")

    relay_once(sessions, flaky)

    assert flaky.ids() == [("transfers.start", event_id)]
    with sessions() as db, db.begin():
        assert db.get_one(OutboxEvent, event_id).published_at is None
        db.execute(update(OutboxEvent).values(next_attempt_at=func.now()))
    working = RecordingPublisher()
    relay_once(sessions, working)
    # The transfer consumer gets it a second time, and ignores it (test_transfers.py).
    assert working.ids() == [("transfers.start", event_id), ("notifications.notify", event_id)]


def test_an_event_nobody_listens_to_is_simply_marked(sessions: Sessions) -> None:  # noqa: F811
    with sessions() as db, db.begin():
        event_id = emit(
            db,
            aggregate_type="order",
            aggregate_id=uuid.uuid4(),
            event_type="something.else",  # type: ignore[arg-type]
            payload={},
        )
    publisher = RecordingPublisher()

    assert relay_once(sessions, publisher).published == 1

    assert publisher.sent == []
    with sessions() as db:
        assert db.get_one(OutboxEvent, event_id).published_at is not None


def test_a_batch_is_oldest_first_and_limited(sessions: Sessions) -> None:  # noqa: F811
    ids = emit_some(sessions, 5)
    publisher = RecordingPublisher()

    assert relay_once(sessions, publisher, batch=3).published == 3
    assert relay_once(sessions, publisher, batch=3).published == 2

    assert [event_id for _, event_id in publisher.ids()] == ids


@pytest.mark.parametrize("retry_count", [1, 2, 5, 8, 9, 20])
def test_backoff_grows_and_is_capped(retry_count: int) -> None:
    ceiling = min(2**retry_count, 300)
    assert backoff(retry_count, rand=lambda: 0.0) == timedelta(seconds=ceiling / 2)
    assert backoff(retry_count, rand=lambda: 1.0) == timedelta(seconds=ceiling)


class SlowPublisher:
    """Holds each publish a moment, so two relays really overlap."""

    def __init__(self) -> None:
        self.sent: list[uuid.UUID] = []
        self.lock = threading.Lock()

    def publish(self, consumer: Consumer, msg: dict[str, Any]) -> None:
        time.sleep(0.01)
        with self.lock:
            self.sent.append(uuid.UUID(msg["id"]))


def test_two_relays_at_once_publish_each_event_once(
    test_engine: Engine,
    committed_rows: Session,  # noqa: F811
) -> None:
    factory = create_session_factory(test_engine)
    ids = emit_some(factory, 60)
    publisher = SlowPublisher()
    barrier = threading.Barrier(2)

    def relay() -> None:
        barrier.wait()
        while relay_once(factory, publisher, batch=10).claimed:
            pass

    threads = [threading.Thread(target=relay) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(publisher.sent) == sorted(ids)
    with factory() as db:
        published = db.scalars(select(OutboxEvent.published_at).where(OutboxEvent.id.in_(ids)))
        assert None not in list(published)
