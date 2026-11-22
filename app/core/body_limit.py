"""Refuse oversized request bodies before anything reads them.

FastAPI parses a multipart upload completely, spooling it to disk, before a route's own code
runs, so a size check inside the route comes too late to stop a 1 GB upload from filling the
disk. This ASGI middleware looks at Content-Length first and answers 413 without reading the
body at all. No endpoint needs more than settings.max_request_bytes (evidence files are up
to 5 MiB plus the multipart wrapping).

A body sent without Content-Length (Transfer-Encoding: chunked) is counted as it arrives
instead, and cut off with the same 413 once it passes the limit. That matters because
FastAPI parses a form before it checks the caller's token: without the count, an anonymous
client could stream gigabytes to the temp directory and only then be told 401.
"""

import contextlib

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.exceptions import problem_response

_TYPE = "urn:ticketloop:problem:file-too-large"


class _BodyTooLarge(Exception):
    """Raised from receive() to stop the app reading a chunked body past the limit."""


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is None:
            await self._counted(scope, receive, send)
            return
        request = Request(scope)
        if declared < 0:
            response = problem_response(
                request, status=400, title="Bad request", detail="Content-Length is not a number."
            )
        elif declared > self.max_bytes:
            response = self._too_large(request)
        else:
            await self.app(scope, receive, send)
            return
        await response(scope, receive, send)

    async def _counted(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the app, counting body bytes as they arrive and stopping it past the limit.

        The app sees the stop as a failed read and answers it (FastAPI with a 400); that
        answer is dropped, and the 413 goes out instead.
        """
        received = 0
        too_large = False
        started = False

        async def counting_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if too_large:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        with contextlib.suppress(_BodyTooLarge):
            await self.app(scope, counting_receive, guarded_send)
        # If a response had already begun there is nothing left to say; the server drops
        # the connection with the unread body.
        if too_large and not started:
            await self._too_large(Request(scope))(scope, receive, send)

    def _too_large(self, request: Request) -> Response:
        return problem_response(
            request,
            status=413,
            title="The file is too large",
            detail=f"Requests are limited to {self.max_bytes} bytes.",
            type_=_TYPE,
        )


def _content_length(scope: Scope) -> int | None:
    """The declared body size; None if absent, -1 if it is not a whole number."""
    for name, value in scope["headers"]:
        if name == b"content-length":
            text = value.decode("latin-1").strip()
            return int(text) if text.isdigit() else -1
    return None
