"""The Celery configuration, the retry rules and the fake ticket provider, without a broker."""

import uuid

import pytest
from celery.exceptions import Reject, Retry

from app.core.config import Settings
from app.infrastructure.external_providers.tickets import (
    FakeTicketProvider,
    TransferOutcomeUnknown,
    TransferRefused,
    TransferRequest,
    TransferUnavailable,
)
from app.modules.outbox.service import CONSUMERS, Consumer, EventType
from app.workers import tasks  # registers the tasks
from app.workers.celery_app import create_celery, dead_letter_queues


def request(key: str = "k") -> TransferRequest:
    return TransferRequest(idempotency_key=key, provider_reference="TKT-1", to_user_id=uuid.uuid4())


# ---------------------------------------------------------------- the Celery app


def test_delivery_is_acknowledged_only_after_the_work() -> None:
    conf = create_celery(Settings()).conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.broker_transport_options == {"confirm_publish": True}
    assert conf.accept_content == ["json"]


def test_every_live_queue_dead_letters_to_its_own_dead_queue() -> None:
    settings = Settings(queue_prefix="t-")
    conf = create_celery(settings).conf
    live = {queue.name: queue for queue in conf.task_queues}
    dead = {queue.name: queue for queue in dead_letter_queues(settings)}

    assert set(live) == {"t-transfers", "t-notifications", "t-maintenance"}
    for name, queue in live.items():
        assert queue.queue_arguments == {
            "x-dead-letter-exchange": "t-ticketloop.dead",
            "x-dead-letter-routing-key": queue.routing_key,
        }
        target = dead[f"{name}.dead"]
        assert (target.exchange.name, target.routing_key) == (
            "t-ticketloop.dead",
            queue.routing_key,
        )


def test_each_consumer_has_a_task_and_a_queue() -> None:
    celery = create_celery(Settings(queue_prefix="t-"))

    for consumer in Consumer:
        route = celery.amqp.router.route({}, consumer.value)
        assert route["queue"].name == f"t-{consumer.value.split('.')[0]}"
    assert {c for consumers in CONSUMERS.values() for c in consumers} == set(Consumer)
    assert set(CONSUMERS) == set(EventType)


def test_the_schedule() -> None:
    schedule = create_celery(Settings()).conf.beat_schedule
    assert {name: (entry["task"], entry["schedule"]) for name, entry in schedule.items()} == {
        "expire-reservations": ("maintenance.expire_reservations", 30.0),
        "expire-listings": ("maintenance.expire_listings", 300.0),
        "reconcile-payments": ("maintenance.reconcile_payments", 120.0),
        "resume-transfers": ("maintenance.resume_transfers", 120.0),
    }


# ---------------------------------------------------------------- retry rules


@pytest.mark.parametrize("retries", [0, 1, 3, 6, 10])
def test_retry_delay_grows_and_is_capped(retries: int) -> None:
    ceiling = min(2.0**retries, 60.0)
    assert tasks.retry_delay(retries, rand=lambda: 0.0) == ceiling / 2
    assert tasks.retry_delay(retries, rand=lambda: 1.0) == ceiling


@pytest.mark.parametrize(
    "message", [None, {}, {"id": "x", "type": "t", "payload": {}}, {"id": str(uuid.uuid4())}]
)
def test_an_unreadable_message_is_rejected_for_good(message: object) -> None:
    with pytest.raises(Reject) as raised:
        tasks._event(message)
    assert raised.value.requeue is False


class FakeTask:
    name = "transfers.start"

    def __init__(self, retries: int) -> None:
        self.request = type("Request", (), {"retries": retries})()

    def retry(self, countdown: float) -> Retry:
        return Retry(when=countdown)


def test_a_database_outage_is_retried_then_given_up() -> None:
    from sqlalchemy.exc import OperationalError

    def down() -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    with pytest.raises(Retry):
        tasks._deliver(FakeTask(retries=0), down)  # type: ignore[arg-type]
    with pytest.raises(Reject) as raised:
        tasks._deliver(FakeTask(retries=tasks.MAX_RETRIES), down)  # type: ignore[arg-type]
    assert raised.value.requeue is False


def test_a_database_restart_of_minutes_is_waited_out() -> None:
    # Review finding: 5 tries gave up after 15 to 31 seconds, and the message went to the
    # dead-letter queue. Even at the shortest delays, the retries now span 5 minutes.
    shortest = sum(tasks.retry_delay(n, rand=lambda: 0.0) for n in range(tasks.MAX_RETRIES))
    assert shortest >= 5 * 60


def test_anything_else_goes_to_the_dead_letter_queue_at_once() -> None:
    def broken() -> None:
        raise KeyError("order_id")

    with pytest.raises(Reject) as raised:
        tasks._deliver(FakeTask(retries=0), broken)  # type: ignore[arg-type]
    assert raised.value.requeue is False


# ---------------------------------------------------------------- the fake provider


def test_the_provider_succeeds_and_answers_a_repeat_the_same_way() -> None:
    provider = FakeTicketProvider()
    first = provider.transfer(request("abc-1"))
    assert provider.transfer(request("abc-1")) == first == "fpv_abc1"
    assert provider.calls["abc-1"] == 2


def test_busy_a_few_times_then_fine() -> None:
    provider = FakeTicketProvider("retryable", failures=2)
    for _ in range(2):
        with pytest.raises(TransferUnavailable):
            provider.transfer(request())
    assert provider.transfer(request()) == "fpv_k"


def test_busy_for_ever() -> None:
    provider = FakeTicketProvider("retryable")
    for _ in range(10):
        with pytest.raises(TransferUnavailable):
            provider.transfer(request())


@pytest.mark.parametrize(
    ("mode", "error"), [("permanent", TransferRefused), ("unknown", TransferOutcomeUnknown)]
)
def test_refused_and_unknown(mode: str, error: type[Exception]) -> None:
    with pytest.raises(error):
        FakeTicketProvider(mode).transfer(request())  # type: ignore[arg-type]


# ---------------------------------------------------------------- counted (M11)


def worker_tasks(outcome: str) -> float:
    from prometheus_client import REGISTRY

    found = REGISTRY.get_sample_value(
        "worker_tasks_total", {"task": FakeTask.name, "outcome": outcome}
    )
    return found or 0.0


@pytest.mark.parametrize(
    ("outcome", "raised"),
    [("done", None), ("retry", Retry(when=1.0)), ("rejected", Reject("poison", requeue=False))],
)
def test_every_delivery_is_counted_by_how_it_ended(outcome: str, raised: Exception | None) -> None:
    before = worker_tasks(outcome)

    def work() -> None:
        if raised is not None:
            raise raised

    if raised is None:
        tasks._counted(FakeTask(retries=0), work)  # type: ignore[arg-type]
    else:
        with pytest.raises(type(raised)):
            tasks._counted(FakeTask(retries=0), work)  # type: ignore[arg-type]

    assert worker_tasks(outcome) == before + 1


def test_every_line_a_task_logs_names_its_event_and_order() -> None:
    # Bound when Celery starts the task and unbound after it has logged how the task ended, so
    # Celery's own "Task ... succeeded" line names the order too.
    import structlog
    from celery.signals import task_postrun, task_prerun

    order_id, event_id = str(uuid.uuid4()), str(uuid.uuid4())
    message = {"id": event_id, "type": "transfer.initiated", "payload": {"order_id": order_id}}

    task_prerun.send(sender=FakeTask, task_id="t-1", task=FakeTask, args=[message], kwargs={})
    during = structlog.contextvars.get_contextvars()
    task_postrun.send(sender=FakeTask, task_id="t-1", task=FakeTask, args=[message], kwargs={})

    assert during["order_id"] == order_id
    assert during["event_id"] == event_id
    assert "order_id" not in structlog.contextvars.get_contextvars()


@pytest.mark.parametrize("args", [[], ["not a message"], [{"id": 1, "payload": "x"}], None])
def test_a_task_without_a_readable_event_logs_without_ids(args: object) -> None:
    import structlog
    from celery.signals import task_postrun, task_prerun

    task_prerun.send(sender=FakeTask, task_id="t-2", task=FakeTask, args=args, kwargs={})
    during = structlog.contextvars.get_contextvars()
    task_postrun.send(sender=FakeTask, task_id="t-2", task=FakeTask, args=args, kwargs={})

    assert "order_id" not in during
    assert structlog.contextvars.get_contextvars() == {}


def test_a_worker_that_starts_before_the_broker_still_serves_its_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # After a reboot Docker starts every container at once, and the worker can start before
    # RabbitMQ answers. Celery logs a start-up receiver's error and carries on, so declaring
    # the dead-letter queues failing must not also skip emptying and serving the metrics.
    from celery.signals import worker_init

    from app.core.config import get_settings
    from app.workers import celery_app as worker

    def broker_down(*_: object) -> None:
        raise OSError("connection refused")

    served: list[int] = []
    monkeypatch.setattr(worker, "declare_dead_letter_queues", broker_down)
    monkeypatch.setattr(worker, "serve_worker_metrics", served.append)

    worker_init.send(sender=None)

    assert served == [get_settings().worker_metrics_port]
