"""What a worker or relay process needs, made once per process on first use.

Lazily, because Celery may fork its pool after importing this: an engine created before the
fork would share its connections between processes.
"""

from functools import cache

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.engine import create_db_engine
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.external_providers.tickets import FakeTicketProvider, TicketProvider
from app.infrastructure.payments import FakepayGateway, PaymentGateway
from app.infrastructure.redis import Breaker, Cache, create_redis


@cache
def sessions() -> sessionmaker[Session]:
    """Their commits bump the cache like the API's: a transfer that sells a listing, or the
    expiry job, changes what search shows."""
    settings = get_settings()
    factory = create_session_factory(create_db_engine(settings))
    factory.configure(info={"cache": search_cache()})
    return factory


@cache
def search_cache() -> Cache | None:
    settings = get_settings()
    if not settings.cache_enabled:
        return None
    ttl = (settings.cache_ttl_min_seconds, settings.cache_ttl_max_seconds)
    client = create_redis(settings)
    return Cache(client, Breaker(probe=client.ping), ttl=ttl)


@cache
def ticket_provider() -> TicketProvider:
    settings = get_settings()
    return FakeTicketProvider(
        settings.ticket_provider_mode,
        settings.ticket_provider_failures,
        settings.ticket_provider_delay_seconds,
    )


@cache
def gateway() -> PaymentGateway:
    return FakepayGateway.at(get_settings().fakepay_url)
