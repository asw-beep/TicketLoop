"""Request context middleware: gives every request an ID and writes one access-log line for it.

This is a plain ASGI middleware rather than Starlette's BaseHTTPMiddleware. An ASGI app is
just `async def app(scope, receive, send)`: `scope` describes the request, `receive` yields
the request body, and `send` is called with the response start (status and headers) and then
the body. A middleware wraps the next app and may change what passes through.

Here we:
1. pick a request ID (the caller's X-Request-ID if it looks safe, otherwise a new one),
2. bind it into structlog's context so every log line during this request includes it,
3. store it on request.state so error handlers can put it in the error body,
4. wrap `send` to add the X-Request-ID header and remember the status code,
5. log one line when the request finishes, even if the app raised.
"""

import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"

# Accept a caller's request ID only if it is short and plain, so nobody can inject newlines or
# fake JSON into our logs through a header.
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")

access_log = structlog.get_logger("ticketloop.access")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # lifespan and websocket messages pass straight through
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # If the app crashes before it starts a response, the server sends a 500.
        status_code = 500
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            log = access_log.error if status_code >= 500 else access_log.info
            log(
                "http.request",
                method=scope["method"],
                path=scope["path"],
                status=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope["headers"]:  # header names arrive lower-cased, as bytes
        if name == b"x-request-id":
            candidate = value.decode("latin-1")
            return candidate if _SAFE_REQUEST_ID.fullmatch(candidate) else None
    return None
