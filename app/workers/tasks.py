"""The Celery tasks: thin wrappers that turn what a consumer did into ack, retry or reject.

The work itself is in the modules (transfers, notifications) as plain functions, which the
tests call directly. Here only the delivery rules live:

- finished, or handled before: return, and the message is acknowledged;
- the database is unavailable, or the provider asks for a retry: try again later, with
  exponential backoff and jitter, for 5 to 10 minutes in all;
- anything else, or out of retries: reject without requeueing, and the message goes to the
  dead-letter queue. A poison message must not loop for ever.
"""

import random
import uuid
from collections.abc import Callable
from typing import Any

import structlog
from celery import Task
from celery.exceptions import Reject
from sqlalchemy.exc import OperationalError

from app.modules.notifications.service import notify
from app.modules.outbox.service import Consumer
from app.modules.transfers.service import Outcome, perform_transfer, start_transfer
from app.workers import jobs, runtime
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)

# 1, 2, 4 ... 32 seconds, then a minute at a time: 5 to 10 minutes of a database restarting.
# The provider's busy answers count here too, but the transfer gives up after its fifth try, so
# at least 11 are left for the database. Beyond that, the message goes to the dead-letter queue
# and the resume_transfers job picks the transfer up again once nothing has moved for 10 minutes.
MAX_RETRIES = 16
MAX_DELAY_SECONDS = 60.0


def retry_delay(retries: int, rand: Callable[[], float] = random.random) -> float:
    """1, 2, 4 ... seconds, at most a minute, each a random 50 to 100% of that."""
    return min(2.0**retries, MAX_DELAY_SECONDS) * (0.5 + rand() / 2)


def _event(message: Any) -> tuple[uuid.UUID, str, dict[str, Any]]:
    try:
        return uuid.UUID(message["id"]), str(message["type"]), dict(message["payload"])
    except (KeyError, TypeError, ValueError) as error:
        log.error("task.unreadable_message", error=repr(error))
        raise Reject(f"unreadable message: {error!r}", requeue=False) from error


def _deliver[T](task: Task, work: Callable[[], T]) -> T:
    try:
        return work()
    except OperationalError as error:
        # Postgres restarting or unreachable: the same message will work later.
        if task.request.retries >= MAX_RETRIES:
            log.error("task.gave_up", task=task.name, error=str(error))
            raise Reject(str(error), requeue=False) from error
        raise task.retry(countdown=retry_delay(task.request.retries)) from error
    except Exception as error:
        log.exception("task.poison", task=task.name)
        raise Reject(f"{type(error).__name__}: {error}", requeue=False) from error


@celery_app.task(name=Consumer.START_TRANSFER.value, bind=True, max_retries=None)
def start_transfer_task(self: Task, message: dict[str, Any]) -> None:
    event_id, _, payload = _event(message)

    def work() -> None:
        with runtime.sessions()() as db, db.begin():
            start_transfer(db, event_id, payload)

    _deliver(self, work)


@celery_app.task(name=Consumer.PERFORM_TRANSFER.value, bind=True, max_retries=None)
def perform_transfer_task(self: Task, message: dict[str, Any]) -> None:
    event_id, _, payload = _event(message)
    outcome = _deliver(
        self,
        lambda: perform_transfer(runtime.sessions(), runtime.ticket_provider(), event_id, payload),
    )
    if outcome == Outcome.RETRY:
        # The transfer counts its own attempts and gives up after the fifth.
        raise self.retry(countdown=retry_delay(self.request.retries))


@celery_app.task(name=Consumer.NOTIFY.value, bind=True, max_retries=None)
def notify_task(self: Task, message: dict[str, Any]) -> None:
    event_id, event_type, payload = _event(message)

    def work() -> None:
        with runtime.sessions()() as db, db.begin():
            notify(db, event_id, event_type, payload)

    _deliver(self, work)


# ------------------------------------------------------------------ scheduled by beat
# A failed run is logged and forgotten: beat starts the next one on time anyway.


def _job(name: str, work: Callable[[], object]) -> None:
    try:
        result = work()
        log.info("job.finished", job=name, result=str(result))
    except Exception:
        log.exception("job.failed", job=name)


@celery_app.task(name="maintenance.expire_reservations")
def expire_reservations_task() -> None:
    _job(
        "expire_reservations",
        lambda: jobs.expire_reservations(runtime.sessions(), runtime.gateway()),
    )


@celery_app.task(name="maintenance.expire_listings")
def expire_listings_task() -> None:
    _job("expire_listings", lambda: jobs.expire_listings(runtime.sessions()))


@celery_app.task(name="maintenance.reconcile_payments")
def reconcile_payments_task() -> None:
    _job(
        "reconcile_payments",
        lambda: jobs.reconcile_payments(runtime.sessions(), runtime.gateway()),
    )


@celery_app.task(name="maintenance.resume_transfers")
def resume_transfers_task() -> None:
    _job("resume_transfers", lambda: jobs.resume_transfers(runtime.sessions()))
