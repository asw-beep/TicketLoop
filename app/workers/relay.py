"""The outbox relay as a process: publish due events, every second, until stopped.

    python -m app.workers.relay            # loop; Ctrl+C to stop
    python -m app.workers.relay --once     # one pass

Run as many as you like: each pass claims its rows with FOR UPDATE SKIP LOCKED.
"""

import argparse
import sys
import time
from typing import Any

import structlog
from celery import Celery
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.infrastructure.database.engine import create_db_engine
from app.infrastructure.database.session import create_session_factory
from app.modules.outbox.relay import BATCH, PublishError, RelayReport, relay_once
from app.modules.outbox.service import Consumer
from app.workers.celery_app import create_celery

log = structlog.get_logger(__name__)

INTERVAL_SECONDS = 1.0


class CeleryPublisher:
    """One Celery task per consumer per event. The task id names both, so the broker's
    management page and the worker's logs say which event a message carries."""

    def __init__(self, app: Celery) -> None:
        self.app = app

    def publish(self, consumer: Consumer, message: dict[str, Any]) -> None:
        try:
            self.app.send_task(
                consumer.value,
                args=[message],
                task_id=f"{message['id']}:{consumer.value}",
                # No retrying in here: the outbox row is the retry, with its backoff.
                retry=False,
            )
        except Exception as error:  # every way a publish can fail is the same to us
            raise PublishError(f"{type(error).__name__}: {error}") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish outbox events to the broker.")
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)
    engine = create_db_engine(settings)
    publisher = CeleryPublisher(create_celery(settings))
    sessions = create_session_factory(engine)
    try:
        while True:
            report: RelayReport | None = None
            try:
                report = relay_once(sessions, publisher)
                if report.published or report.failed or args.once:
                    log.info(
                        "outbox.relayed",
                        published=report.published,
                        failed=report.failed,
                        error=report.error,
                    )
            except OperationalError as error:
                if args.once:
                    raise
                log.warning("outbox.database_unavailable", error=str(error.orig))
            if args.once:
                return 0
            # A full batch that all went out means there is probably more: go again at once.
            if report is None or report.claimed < BATCH or report.failed:
                time.sleep(INTERVAL_SECONDS)
    except KeyboardInterrupt:
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
