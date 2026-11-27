"""No more requests inside the app at once than there are threads to run them.

FastAPI runs a sync route in hops on anyio's thread pool (40 threads): each sync dependency,
then the route, then the response's validation, each hop waiting for a free thread. The
request's database session holds its connection from its first query until get_db commits,
after the response is built. A burst bigger than the pool then deadlocks. Every thread is
waiting in the pool for a connection, and every connection belongs to a request waiting for a
thread for its next hop, until the pool's 30-second timeout turns them into 500s. M12's spike
(1,000 buyers at once on four API processes) got 131 500s and 688 timeouts that way.

With no more requests inside than there are threads, a request always finds a thread for its
next hop, so whoever holds a connection finishes and gives it back. The others wait here, in
the order they came, holding nothing. /metrics goes straight through: it reads Postgres on a
connection of its own, and Prometheus should see a busy API rather than time out on it.
"""

import anyio
from anyio import to_thread
from anyio.lowlevel import RunVar
from starlette.types import ASGIApp, Receive, Scope, Send

UNLIMITED_PATHS = frozenset({"/metrics"})

# One per event loop, sized from that loop's thread limiter when it first admits a request.
_admitted: RunVar[anyio.Semaphore] = RunVar("ticketloop_admitted")


def _semaphore() -> anyio.Semaphore:
    try:
        return _admitted.get()
    except LookupError:
        threads = int(to_thread.current_default_thread_limiter().total_tokens)
        semaphore = anyio.Semaphore(threads)
        _admitted.set(semaphore)
        return semaphore


class AdmissionMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in UNLIMITED_PATHS:
            await self.app(scope, receive, send)
            return
        async with _semaphore():
            await self.app(scope, receive, send)
