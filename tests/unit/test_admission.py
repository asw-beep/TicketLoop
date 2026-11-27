"""AdmissionMiddleware: no more requests inside than there are threads; /metrics always in."""

import anyio
from anyio import to_thread
from starlette.types import Receive, Scope, Send

from app.core.admission import AdmissionMiddleware


def http(path: str) -> Scope:
    return {"type": "http", "path": path}


async def nothing() -> dict[str, object]:
    return {"type": "http.request"}


async def ignore(_: object) -> None:
    pass


def test_with_every_thread_taken_a_request_waits_and_metrics_does_not() -> None:
    inside: list[str] = []
    release = anyio.Event()

    async def app(scope: Scope, _receive: Receive, _send: Send) -> None:
        inside.append(scope["path"])
        if scope["path"] == "/api/v1/first":
            await release.wait()

    admission = AdmissionMiddleware(app)

    async def scenario() -> list[list[str]]:
        to_thread.current_default_thread_limiter().total_tokens = 1
        seen = []
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(admission, http("/api/v1/first"), nothing, ignore)
            await anyio.wait_all_tasks_blocked()
            tasks.start_soon(admission, http("/api/v1/second"), nothing, ignore)
            tasks.start_soon(admission, http("/metrics"), nothing, ignore)
            await anyio.wait_all_tasks_blocked()
            seen.append(list(inside))  # the second waits; /metrics went in
            release.set()
            await anyio.wait_all_tasks_blocked()
            seen.append(list(inside))
        return seen

    while_full, after = anyio.run(scenario)
    assert while_full == ["/api/v1/first", "/metrics"]
    assert after == ["/api/v1/first", "/metrics", "/api/v1/second"]


def test_lifespan_messages_pass_straight_through() -> None:
    seen: list[str] = []

    async def app(scope: Scope, _receive: Receive, _send: Send) -> None:
        seen.append(scope["type"])

    anyio.run(AdmissionMiddleware(app), {"type": "lifespan"}, nothing, ignore)
    assert seen == ["lifespan"]
