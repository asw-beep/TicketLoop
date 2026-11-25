"""The API with the reserve strategy named in RESERVE_STRATEGY. `uvicorn` runs this one in the
comparison, and the app itself everywhere else. Adds an X-Attempts header: how many times the
request ran its transaction."""

import os

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.main import create_app
from tests.concurrency import strategies


class CountAttempts:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        count = [0]
        strategies.attempts.set(count)

        async def send_with_count(message: Message) -> None:
            if message["type"] == "http.response.start":
                header = (b"x-attempts", str(max(count[0], 1)).encode())
                message["headers"] = [*message["headers"], header]
            await send(message)

        await self.app(scope, receive, send_with_count)


strategies.install(os.environ["RESERVE_STRATEGY"])
app = create_app()
app.add_middleware(CountAttempts)
