"""The API the browser tests talk to, on a database of its own.

Playwright starts this (frontend/playwright.config.ts). Every run drops `ticketloop_e2e`,
creates it and migrates it, so a run never sees rows an earlier run, or a person, left behind.
Then it starts a Celery worker and the outbox relay beside the API, so a paid order goes on to
hand the ticket over, as in Docker. They use the queues of QUEUE_PREFIX (env.ts sets "e2e-"),
so a development worker on the same RabbitMQ never takes their messages. And it empties the
Redis database of REDIS_URL (env.ts: database 1), so no cached page from an earlier run, built
on an earlier database, is served to this one.

    DATABASE_URL=.../ticketloop_e2e QUEUE_PREFIX=e2e- uv run python -m scripts.e2e_api --port 8001
"""

import argparse
import subprocess
import sys

import uvicorn
from redis.exceptions import RedisError
from sqlalchemy import make_url

from app.core.config import Settings, get_settings
from app.infrastructure.database.engine import create_db_engine
from app.infrastructure.redis import create_redis
from app.workers.celery_app import QUEUES
from scripts.databases import is_throwaway, recreate_database, upgrade


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args(argv)

    settings = get_settings()
    name = make_url(settings.database_url).database
    if not is_throwaway(name, ("_e2e",)):
        print(f"Refusing: {name!r} does not end in _e2e. Set DATABASE_URL.", file=sys.stderr)
        return 2
    recreate_database(settings.database_url)
    engine = create_db_engine(settings)
    upgrade(engine)
    engine.dispose()
    empty_redis(settings)

    prefix = settings.queue_prefix
    queues = ",".join(f"{prefix}{name}" for name in QUEUES)
    worker = [sys.executable, "-m", "celery", "-A", "app.workers.celery_app", "worker"]
    # solo: one task at a time in the worker's own process, the pool Celery runs on Windows.
    worker += ["--pool=solo", "-Q", queues, "-n", f"e2e@{prefix or 'default'}"]
    worker += ["--without-gossip", "--without-mingle", "--without-heartbeat"]
    worker += ["--loglevel", "WARNING"]
    helpers = [
        subprocess.Popen(worker),
        subprocess.Popen([sys.executable, "-m", "app.workers.relay"]),
    ]
    try:
        uvicorn.run("app.main:app", host="127.0.0.1", port=args.port)
    finally:
        for process in helpers:
            process.kill()
    return 0


def empty_redis(settings: Settings) -> None:
    client = create_redis(settings)
    if client.connection_pool.connection_kwargs.get("db", 0) == 0:
        print("Not emptying Redis database 0: set REDIS_URL to another.", file=sys.stderr)
        return
    try:
        client.flushdb()
    except RedisError as error:  # the API runs without it, slower
        print(f"Redis not emptied: {error}", file=sys.stderr)
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
