"""Cached reads for routes: cache-aside, answered as raw JSON with an `X-Cache` header.

`X-Cache` says what happened, so a hit ratio can be measured from outside (k6, curl):
HIT from Redis, MISS computed and stored, BYPASS computed without the cache (switched off,
Redis down, or a page this route does not cache).
"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from fastapi import Request, Response
from pydantic import BaseModel

from app.infrastructure.redis import Cache, stats


def cached_json(
    request: Request,
    *,
    name: str,
    depends_on: Sequence[str],
    params: Mapping[str, Any],
    compute: Callable[[], BaseModel],
) -> Response:
    """The JSON of `compute()`, from Redis when it holds it for exactly these `params` and
    the current versions of `depends_on`."""
    cache: Cache | None = getattr(request.app.state, "cache", None)
    seen = cache.versions(*depends_on) if cache is not None and cache.enabled else None
    if cache is None or seen is None:
        stats.add("cache_bypassed")
        return _answer(compute().model_dump_json().encode(), "BYPASS")

    # The versions are read before Postgres is: see app/infrastructure/redis/cache.py.
    query = json.dumps(dict(params), sort_keys=True, default=str)
    versions = ":".join(f"{n}{v}" for n, v in zip(depends_on, seen, strict=True))
    key = f"cache:{name}:{versions}:{hashlib.sha256(query.encode()).hexdigest()[:32]}"

    found = cache.get(key)
    if found is not None:
        stats.add("cache_hits")
        return _answer(found, "HIT")
    body = compute().model_dump_json().encode()
    cache.set(key, body)
    stats.add("cache_misses")
    return _answer(body, "MISS")


def uncached(compute: Callable[[], BaseModel]) -> Response:
    """The same shape and header for a page that is never cached (a later page of results)."""
    stats.add("cache_bypassed")
    return _answer(compute().model_dump_json().encode(), "BYPASS")


def _answer(body: bytes, outcome: str) -> Response:
    return Response(content=body, media_type="application/json", headers={"X-Cache": outcome})
