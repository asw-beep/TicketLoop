"""Redis (M10): a cache for the public reads and a rate limiter.

Neither is ever part of a decision about a ticket or money: a stale cached search can send a
buyer to a listing, and Postgres then says whether it is still for sale. Both fail open. With
Redis down the cache answers from Postgres and the limiter lets everything through, and the
breaker keeps either from waiting on Redis for every request.
"""

from app.infrastructure.redis.breaker import Breaker
from app.infrastructure.redis.cache import Cache
from app.infrastructure.redis.client import create_redis
from app.infrastructure.redis.limiter import Decision, TokenBucket
from app.infrastructure.redis.stats import stats

__all__ = ["Breaker", "Cache", "Decision", "TokenBucket", "create_redis", "stats"]
