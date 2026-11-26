"""The Celery application: queues, the settings that make delivery reliable, and beat.

    celery -A app.workers.celery_app worker -Q transfers,notifications,maintenance
    celery -A app.workers.celery_app beat

Reliability, setting by setting:

- `task_acks_late`: a message is acknowledged after its task has finished, not when it
  arrives. A worker that dies mid-task leaves it unacknowledged, and RabbitMQ delivers it
  again. Consumers write processed_events with their effect, so the second run does nothing.
- `task_reject_on_worker_lost`: the same when the worker process is killed outright.
- `worker_prefetch_multiplier = 1`: a worker holds one message beyond the one it is running,
  so a crash strands little and a slow task does not starve the others.
- `confirm_publish`: send_task waits for the broker to say it has the message. The relay marks
  an outbox event published only after that.
- Every live queue has a dead-letter exchange. A task that gives up rejects its message
  without requeueing it, and RabbitMQ moves it to `<queue>.dead` for a person to look at.

`QUEUE_PREFIX` puts every exchange and queue in a namespace of its own, so the tests can share
a broker with a running development worker without either eating the other's messages.
"""

from celery import Celery
from celery.signals import setup_logging, worker_init
from kombu import Exchange, Queue

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging

QUEUES = ("transfers", "notifications", "maintenance")


def dead_letter_queues(settings: Settings) -> list[Queue]:
    dead = Exchange(f"{settings.queue_prefix}ticketloop.dead", type="direct", durable=True)
    return [
        Queue(f"{settings.queue_prefix}{name}.dead", dead, routing_key=name, durable=True)
        for name in QUEUES
    ]


def create_celery(settings: Settings) -> Celery:
    prefix = settings.queue_prefix
    exchange = Exchange(f"{prefix}ticketloop", type="direct", durable=True)
    live = [
        Queue(
            f"{prefix}{name}",
            exchange,
            routing_key=name,
            durable=True,
            queue_arguments={
                "x-dead-letter-exchange": f"{prefix}ticketloop.dead",
                "x-dead-letter-routing-key": name,
            },
        )
        for name in QUEUES
    ]
    app = Celery("ticketloop", broker=settings.broker_url, include=["app.workers.tasks"])
    app.conf.update(
        # Only the live queues: a worker consumes every queue listed here.
        task_queues=live,
        task_default_queue=f"{prefix}maintenance",
        task_default_exchange=exchange.name,
        task_default_routing_key="maintenance",
        task_routes={
            "transfers.*": {"queue": f"{prefix}transfers"},
            "notifications.*": {"queue": f"{prefix}notifications"},
            "maintenance.*": {"queue": f"{prefix}maintenance"},
        },
        task_serializer="json",
        accept_content=["json"],
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_transport_options={"confirm_publish": True},
        broker_connection_timeout=settings.broker_connection_timeout_seconds,
        broker_connection_retry_on_startup=True,
        worker_hijack_root_logger=False,
        # Our logging writes to stdout itself; redirected, every line would come out twice.
        worker_redirect_stdouts=False,
        timezone="UTC",
        beat_schedule={
            "expire-reservations": {
                "task": "maintenance.expire_reservations",
                "schedule": 30.0,
            },
            "expire-listings": {"task": "maintenance.expire_listings", "schedule": 300.0},
            "reconcile-payments": {"task": "maintenance.reconcile_payments", "schedule": 120.0},
            # Transfers whose message ended in a dead-letter queue.
            "resume-transfers": {"task": "maintenance.resume_transfers", "schedule": 120.0},
        },
    )
    return app


def declare_dead_letter_queues(app: Celery, settings: Settings) -> None:
    """The queues rejected messages land in. Nobody consumes them; a person reads them."""
    with app.connection_for_write() as connection:
        channel = connection.default_channel
        for queue in dead_letter_queues(settings):
            queue(channel).declare()


celery_app = create_celery(get_settings())


@setup_logging.connect
def _set_up_logging(**_: object) -> None:
    """Connected, this replaces Celery's own logging setup, in the worker and in beat: the same
    JSON lines with request IDs as the API."""
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)


@worker_init.connect
def _on_worker_start(**_: object) -> None:
    declare_dead_letter_queues(celery_app, get_settings())
