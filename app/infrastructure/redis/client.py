from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.core.config import Settings


def create_redis(settings: Settings) -> Redis:
    """A client that gives up fast. Nothing here is worth a slow request: a cache miss costs a
    query, and a limiter that cannot ask lets the request through. No connection is made until
    the first command."""
    timeout = settings.redis_timeout_seconds
    return Redis.from_url(
        settings.redis_url,
        socket_timeout=timeout,
        socket_connect_timeout=timeout,
        # redis-py retries three times by default; one timeout is enough to know.
        retry=Retry(NoBackoff(), 0),
        health_check_interval=30,
    )
