"""Rate limits on the endpoints worth abusing: guessing passwords, holding tickets, starting
payments, scraping search. A refusal is a 429 problem with `Retry-After` in seconds.

The address is the socket's, never `X-Forwarded-For`: anyone can send that header, and a
limiter that trusts it is a limiter anyone can step around. Behind a proxy this would change,
and only for a proxy we run.
"""

from collections.abc import Callable

import structlog
from fastapi import Request

from app.core.exceptions import AppError
from app.infrastructure.redis import TokenBucket
from app.infrastructure.redis.limiter import login_identity, retry_after_seconds
from app.modules.identity.dependencies import CurrentUser

log = structlog.get_logger(__name__)


class RateLimitedError(AppError):
    status = 429
    code = "rate-limited"
    title = "Too many requests"

    def __init__(self, retry_after: int) -> None:
        # The wait is in the header, not the words, so two refusals read the same.
        super().__init__("Too many attempts. Wait a moment, then try again.")
        self.headers = {"Retry-After": str(retry_after)}


def enforce(request: Request, name: str, identity: str, per_minute: int | None = None) -> None:
    """Take a token from `identity`'s bucket for `name`, or refuse with 429."""
    settings = request.app.state.settings
    if not settings.rate_limit_enabled:
        return
    limit = per_minute or getattr(settings, f"rate_limit_{name}_per_minute")
    limiter: TokenBucket = request.app.state.limiter
    decision = limiter.take(name, identity, limit)
    if not decision.allowed:
        log.info("rate_limit.refused", limit=name)
        raise RateLimitedError(retry_after_seconds(decision.retry_after))


def address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def per_user(name: str) -> Callable[[Request, CurrentUser], None]:
    def limit(request: Request, user: CurrentUser) -> None:
        enforce(request, name, str(user.id))

    return limit


def per_address(name: str) -> Callable[[Request], None]:
    def limit(request: Request) -> None:
        enforce(request, name, address(request))

    return limit


def limit_login(request: Request, email: str, name: str = "login") -> None:
    """Login and password-reset requests: per email and address, before anything is checked,
    so a refusal is the same for an account that exists and one that does not."""
    settings = request.app.state.settings
    enforce(
        request,
        name,
        login_identity(email, address(request)),
        per_minute=settings.rate_limit_login_per_minute,
    )
