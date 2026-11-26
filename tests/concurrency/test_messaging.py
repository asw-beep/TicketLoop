"""M9's drills, automated: a real RabbitMQ, real Celery worker processes, real commits.

- A purchase through the API and fakepay ends with the ticket in the buyer's name.
- The broker unreachable while orders are paid: every event waits, then all are published,
  and each transfer happens once.
- A worker killed in the middle of a transfer: another takes the message over, and the ticket
  moves once.
- A poison message: rejected into the dead-letter queue, not retried for ever.

The workers and the relay use a queue prefix of their own, so a development worker on the
same broker never sees these messages.
"""

import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any, Self

import httpx2 as httpx
import pytest
from kombu import Connection
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.infrastructure.database.session import create_session_factory
from app.modules.audit.models import AuditLog
from app.modules.listings.models import Listing
from app.modules.notifications.models import Notification
from app.modules.orders.models import Order
from app.modules.outbox.models import OutboxEvent
from app.modules.outbox.relay import relay_once
from app.modules.payments import service as payments
from app.modules.tickets.models import Ticket
from app.modules.transfers.models import Transfer
from app.workers.celery_app import QUEUES, create_celery, dead_letter_queues
from app.workers.relay import CeleryPublisher
from tests.concurrency.harness import REPO_ROOT, ApiServer, Buyer, free_port, make_active_listing
from tests.integration.factories import make_payment

pytestmark = pytest.mark.integration

WAIT_SECONDS = 60


# ---------------------------------------------------------------- the broker and its workers


@pytest.fixture(scope="module")
def broker(race_settings: Settings) -> Iterator[Settings]:
    """Settings for a namespace of our own on the broker; skipped without one. Its queues and
    exchanges are deleted afterwards."""
    try:
        with Connection(race_settings.broker_url, connect_timeout=3) as connection:
            connection.ensure_connection(max_retries=1)
    except Exception as error:
        if os.environ.get("REQUIRE_BROKER") == "1":
            pytest.fail(f"RabbitMQ is required but unreachable: {error}")
        pytest.skip(f"RabbitMQ unreachable ({error}); docker compose up -d rabbitmq")
    prefix = f"test{uuid.uuid4().hex[:8]}-"
    yield race_settings.model_copy(update={"queue_prefix": prefix})
    with Connection(race_settings.broker_url) as connection:
        channel = connection.channel()
        for name in QUEUES:
            for queue in (f"{prefix}{name}", f"{prefix}{name}.dead"):
                channel.queue_delete(queue=queue)
        for exchange in (f"{prefix}ticketloop", f"{prefix}ticketloop.dead"):
            channel.exchange_delete(exchange=exchange)


class Worker:
    """`with Worker(settings) as worker:` one Celery worker process, solo pool."""

    def __init__(self, settings: Settings, name: str, **env: str) -> None:
        prefix = settings.queue_prefix
        queues = ",".join(f"{prefix}{q}" for q in ("transfers", "notifications", "maintenance"))
        self.command = [
            sys.executable, "-m", "celery", "-A", "app.workers.celery_app", "worker",
            "--pool=solo", "-Q", queues, "-n", f"{name}@{prefix}",
            "--without-gossip", "--without-mingle", "--without-heartbeat",
            "--loglevel", "WARNING",
        ]  # fmt: skip
        self.env = os.environ | {
            "DATABASE_URL": settings.database_url,
            "BROKER_URL": settings.broker_url,
            "QUEUE_PREFIX": prefix,
            "ENVIRONMENT": "test",
            "LOG_LEVEL": "WARNING",
            **env,
        }

    def __enter__(self) -> Self:
        self.process = subprocess.Popen(self.command, cwd=REPO_ROOT, env=self.env)
        return self

    def kill(self) -> None:
        self.process.kill()
        self.process.wait(timeout=10)

    def __exit__(self, *_: object) -> None:
        if self.process.poll() is None:
            self.kill()


@pytest.fixture(scope="module")
def sessions(race_engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(race_engine)


@pytest.fixture(scope="module")
def publisher(broker: Settings) -> Iterator[CeleryPublisher]:
    app = create_celery(broker)
    yield CeleryPublisher(app)
    app.close()


def relay_until(
    sessions: sessionmaker[Session], publisher: CeleryPublisher, done: Callable[[], bool]
) -> None:
    """Relay whatever is due, again and again, until `done()` or the time runs out."""
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        relay_once(sessions, publisher)
        if done():
            return
        time.sleep(0.2)
    raise AssertionError("not done in time")


def paid_orders(sessions: sessionmaker[Session], count: int) -> list[uuid.UUID]:
    """Orders paid through the real settling code, each with its payment.succeeded event."""
    ids = []
    with sessions() as db, db.begin():
        for _ in range(count):
            payment = make_payment(db, status="PENDING")
            payments.succeed(db, payment.id, source="test", snapshot={})
            ids.append(payment.order_id)
    return ids


def statuses(sessions: sessionmaker[Session], order_ids: list[uuid.UUID]) -> list[str]:
    with sessions() as db:
        return list(db.scalars(select(Order.status).where(Order.id.in_(order_ids))))


def completed(sessions: sessionmaker[Session], order_ids: list[uuid.UUID]) -> Callable[[], bool]:
    return lambda: statuses(sessions, order_ids) == ["COMPLETED"] * len(order_ids)


def moves(db: Session, order_id: uuid.UUID) -> int:
    """How many times the order's ticket changed hands."""
    ticket_id = db.scalar(
        select(Listing.ticket_id)
        .join(Order, Order.listing_id == Listing.id)
        .where(Order.id == order_id)
    )
    return db.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.entity_id == ticket_id, AuditLog.action == "ticket.transferred")
    )


def check_sold_once(sessions: sessionmaker[Session], order_id: uuid.UUID) -> None:
    with sessions() as db:
        order = db.get_one(Order, order_id)
        listing = db.get_one(Listing, order.listing_id)
        ticket = db.get_one(Ticket, listing.ticket_id)
        transfers = db.scalars(select(Transfer).where(Transfer.order_id == order_id)).all()
        assert (order.status, listing.status) == ("COMPLETED", "SOLD")
        assert ticket.current_owner_id == order.buyer_id
        assert [t.status for t in transfers] == ["COMPLETED"]
        assert moves(db, order_id) == 1


# ---------------------------------------------------------------- the drills


def test_a_purchase_through_the_api_ends_with_the_ticket_in_the_buyers_name(
    broker: Settings,
    sessions: sessionmaker[Session],
    publisher: CeleryPublisher,
    race_engine: Engine,
    api_server: ApiServer,
    fakepay_server: Any,
    buyers: list[Buyer],
) -> None:
    listing = make_active_listing(race_engine)
    buyer = buyers[0]
    api = api_server.urls[0]
    headers = buyer.headers
    hold = httpx.post(f"{api}/api/v1/listings/{listing.id}/reservations", headers=headers).json()
    order = httpx.post(
        f"{api}/api/v1/orders",
        headers=headers | {"Idempotency-Key": uuid.uuid4().hex},
        json={"reservation_id": hold["id"]},
    ).json()
    intent = httpx.post(
        f"{api}/api/v1/payments/intents",
        headers=headers | {"Idempotency-Key": uuid.uuid4().hex},
        json={"order_id": order["id"]},
    ).json()
    paid = httpx.post(
        f"{fakepay_server.url}/v1/intents/{intent['provider_transaction_id']}/outcome",
        json={"outcome": "succeed"},
    )
    assert paid.status_code == 200
    order_id = uuid.UUID(order["id"])

    def told() -> list[str]:
        with sessions() as db:
            return sorted(
                db.scalars(
                    select(Notification.notification_type).where(Notification.user_id == buyer.id)
                )
            )

    # ticket_received follows transfer.completed, one relay pass after the order completes.
    with Worker(broker, "purchase"):
        relay_until(
            sessions,
            publisher,
            lambda: (
                completed(sessions, [order_id])()
                and told() == ["payment_succeeded", "ticket_received"]
            ),
        )

    check_sold_once(sessions, order_id)
    seen = httpx.get(f"{api}/api/v1/orders/{order_id}", headers=headers).json()
    assert seen["status"] == "COMPLETED"
    assert seen["transfer"]["status"] == "COMPLETED"


def test_a_dead_broker_keeps_every_event_and_a_live_one_gets_them_all(
    broker: Settings, sessions: sessionmaker[Session], publisher: CeleryPublisher
) -> None:
    order_ids = paid_orders(sessions, 10)
    dead = broker.model_copy(update={"broker_url": f"amqp://guest:guest@127.0.0.1:{free_port()}//"})
    nowhere = CeleryPublisher(create_celery(dead))

    for _ in range(3):
        report = relay_once(sessions, nowhere)
        assert (report.published, report.failed) == (0, 1)

    with sessions() as db:
        waiting = db.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "payment.succeeded",
                OutboxEvent.aggregate_id.in_(order_ids),
            )
        ).all()
        assert len(waiting) == 10
        assert all(event.published_at is None for event in waiting)
        assert sum(event.retry_count for event in waiting) == 3
    # The broker is back. Waiting events are due again after their backoff; skip the wait.
    with sessions() as db, db.begin():
        db.execute(update(OutboxEvent).values(next_attempt_at=func.now()))

    with Worker(broker, "after-outage"):
        relay_until(sessions, publisher, completed(sessions, order_ids))

    for order_id in order_ids:
        check_sold_once(sessions, order_id)


def test_a_worker_killed_mid_transfer_is_replaced_and_the_ticket_moves_once(
    broker: Settings, sessions: sessionmaker[Session], publisher: CeleryPublisher
) -> None:
    (order_id,) = paid_orders(sessions, 1)

    def processing() -> bool:
        with sessions() as db:
            return (
                db.scalar(select(Transfer.status).where(Transfer.order_id == order_id))
                == "PROCESSING"
            )

    # The first worker is slow at the provider, and dies there.
    with Worker(broker, "doomed", TICKET_PROVIDER_DELAY_SECONDS="30") as doomed:
        relay_until(sessions, publisher, processing)
        doomed.kill()

    with Worker(broker, "survivor"):
        relay_until(sessions, publisher, completed(sessions, [order_id]))

    check_sold_once(sessions, order_id)
    with sessions() as db:
        transfer = db.scalars(select(Transfer).where(Transfer.order_id == order_id)).one()
        # Asked twice, with the same key: once by the worker that died, once by its successor.
        assert transfer.attempt_count == 2


def dead_letters(broker: Settings, queue: str) -> int:
    with Connection(broker.broker_url) as connection:
        channel = connection.channel()
        name = f"{broker.queue_prefix}{queue}.dead"
        return channel.queue_declare(queue=name, passive=True).message_count


@pytest.mark.parametrize(
    "message",
    [
        {
            "id": str(uuid.uuid4()),
            "type": "payment.succeeded",
            "payload": {"order_id": str(uuid.uuid4())},
        },
        "not an event at all",
    ],
    ids=["about-nothing", "unreadable"],
)
def test_a_poison_message_ends_in_the_dead_letter_queue(
    broker: Settings, publisher: CeleryPublisher, message: Any
) -> None:
    with Worker(broker, "poison"):
        # The worker declares the dead-letter queues when it starts.
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            try:
                before = dead_letters(broker, "transfers")
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        publisher.app.send_task("transfers.start", args=[message], task_id=str(uuid.uuid4()))

        deadline = time.monotonic() + WAIT_SECONDS
        while dead_letters(broker, "transfers") == before:
            assert time.monotonic() < deadline, "the message never reached the dead-letter queue"
            time.sleep(0.2)
    assert dead_letters(broker, "transfers") == before + 1
    assert {q.name for q in dead_letter_queues(broker)} >= {f"{broker.queue_prefix}transfers.dead"}
