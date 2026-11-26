"""A token bucket per endpoint and caller, in one Lua script.

Each bucket holds up to `per_minute` tokens and refills at `per_minute` a minute. A request
takes one; with none left it is refused, told how long until the next. The script reads, refills,
takes and writes in one step, so two requests at once cannot both take the last token, and it
uses Redis's own clock, so four API processes with four clocks agree.
"""

import hashlib
import math
from dataclasses import dataclass

from redis import Redis
from redis.exceptions import RedisError

from app.infrastructure.redis.breaker import Breaker
from app.infrastructure.redis.stats import stats

TAKE = """
local capacity = tonumber(ARGV[1])
local per_ms = tonumber(ARGV[2])
local time = redis.call('TIME')
local now = tonumber(time[1]) * 1000 + math.floor(tonumber(time[2]) / 1000)
local bucket = redis.call('HMGET', KEYS[1], 'tokens', 'at')
local tokens = tonumber(bucket[1]) or capacity
local at = tonumber(bucket[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - at) * per_ms)
local wait_ms = 0
if tokens >= 1 then
  tokens = tokens - 1
else
  wait_ms = math.ceil((1 - tokens) / per_ms)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'at', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity / per_ms) + 1000)
return wait_ms
"""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after: float = 0.0  # seconds until a token is back, when refused


class TokenBucket:
    def __init__(self, client: Redis | None, breaker: Breaker) -> None:
        self._breaker = breaker
        # register_script sends nothing yet; it runs EVALSHA, and loads the script if needed.
        self._take = client.register_script(TAKE) if client is not None else None

    def take(self, name: str, identity: str, per_minute: int) -> Decision:
        """Take a token from the `name` bucket of `identity`. Allowed when Redis cannot say."""
        if self._take is None or not self._breaker.closed():
            stats.add("rate_limit_skipped")
            return Decision(allowed=True)
        try:
            wait_ms = int(
                self._take(keys=[f"rl:{name}:{identity}"], args=[per_minute, per_minute / 60_000])
            )
        except RedisError as error:
            stats.add("rate_limit_errors")
            self._breaker.trip(error)
            return Decision(allowed=True)
        if wait_ms == 0:
            stats.add("rate_limit_allowed")
            return Decision(allowed=True)
        stats.add("rate_limit_refused")
        return Decision(allowed=False, retry_after=wait_ms / 1000)


def login_identity(email: str, address: str) -> str:
    """The login bucket: one per email and address. The email is hashed so the key names no
    one, and normalised so "Asha@x.com " and "asha@x.com" share a bucket."""
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:24]
    return f"{digest}:{address}"


def retry_after_seconds(wait: float) -> int:
    """Retry-After takes whole seconds. Rounded up, and never 0, which would invite a retry
    that is refused again."""
    return max(1, math.ceil(wait))
