"""fakepay: a pretend payment gateway, so TicketLoop can be tested against everything a real one
does wrong.

    uv run uvicorn fakepay.main:app --port 8090

The API calls /v1/... to create and look up payment intents. A buyer's browser is sent to
/checkout/{transaction_id}, where whoever is testing picks what happens. It then tells the API
by a signed webhook, or a late one, or two, or a forged one, or none at all.

It shares nothing with the app but the webhook secret: not its code, not its database.
"""

import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, Form, Header, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, HttpUrl

from fakepay.config import FakepaySettings
from fakepay.page import checkout_page
from fakepay.store import Intent, Settled, Status, Store
from fakepay.webhooks import WebhookSender

Outcome = Literal["succeed", "fail", "delay", "twice", "bad_signature", "no_webhook"]


class IntentIn(BaseModel):
    amount_paise: int = Field(gt=0)
    currency: Literal["INR"]
    return_url: HttpUrl


class OutcomeIn(BaseModel):
    outcome: Outcome


class IntentOut(BaseModel):
    transaction_id: str
    idempotency_key: str
    amount_paise: int
    currency: str
    status: Status
    checkout_url: str
    expires_at: float


def create_app(
    settings: FakepaySettings | None = None,
    sender: WebhookSender | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    settings = settings or FakepaySettings()
    sender = sender or WebhookSender(settings)
    store = Store(settings.db)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        store.close()

    app = FastAPI(title="fakepay", lifespan=lifespan)
    app.state.store = store

    def shown(intent: Intent) -> IntentOut:
        return IntentOut(
            transaction_id=intent.transaction_id,
            idempotency_key=intent.idempotency_key,
            amount_paise=intent.amount_paise,
            currency=intent.currency,
            status=intent.status(clock()),
            checkout_url=f"{settings.public_url}/checkout/{intent.transaction_id}",
            expires_at=intent.expires_at,
        )

    def found(intent: Intent | None) -> Intent:
        if intent is None:
            raise HTTPException(404, "No such payment intent.")
        return intent

    def choose(transaction_id: str, outcome: Outcome) -> Intent:
        settled: Settled = "failed" if outcome == "fail" else "succeeded"
        intent = store.settle(transaction_id, settled, clock())
        if intent is None:
            status = found(store.get(transaction_id)).status(clock())
            raise HTTPException(409, f"The payment intent is {status}.")
        body = sender.event(intent)
        match outcome:
            case "succeed" | "fail":
                sender.send(body)
            case "delay":
                sender.send(body, after=settings.delay_seconds)
            case "twice":
                sender.send(body)
                sender.send(body)
            case "bad_signature":
                sender.send(body, forged=True)
            case "no_webhook":
                pass
        return intent

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/intents", status_code=201)
    def create_intent(
        body: IntentIn,
        response: Response,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=255)],
    ) -> IntentOut:
        """The same key always returns the same intent: a caller that lost our answer asks
        again without risking a second payment."""
        now = clock()
        new = Intent(
            transaction_id=f"txn_{uuid.uuid4().hex}",
            idempotency_key=idempotency_key,
            amount_paise=body.amount_paise,
            currency=body.currency,
            return_url=str(body.return_url),
            settled=None,
            created_at=now,
            expires_at=now + settings.intent_minutes * 60,
        )
        intent = store.add(new)
        if intent.transaction_id != new.transaction_id:
            if (intent.amount_paise, intent.currency) != (new.amount_paise, new.currency):
                raise HTTPException(409, "This key was used for a different payment.")
            response.status_code = 200
        return shown(intent)

    @app.get("/v1/intents")
    def find_intent(idempotency_key: str) -> IntentOut:
        return shown(found(store.by_key(idempotency_key)))

    @app.get("/v1/intents/{transaction_id}")
    def get_intent(transaction_id: str) -> IntentOut:
        return shown(found(store.get(transaction_id)))

    @app.post("/v1/intents/{transaction_id}/outcome")
    def choose_outcome(transaction_id: str, body: OutcomeIn) -> IntentOut:
        """What the checkout page's buttons do, for tests and load tests."""
        return shown(choose(transaction_id, body.outcome))

    @app.get("/checkout/{transaction_id}", response_class=HTMLResponse)
    def checkout(transaction_id: str) -> HTMLResponse:
        intent = found(store.get(transaction_id))
        return HTMLResponse(
            checkout_page(intent, intent.status(clock())), headers={"Cache-Control": "no-store"}
        )

    @app.post("/checkout/{transaction_id}")
    def pay(transaction_id: str, outcome: Annotated[Outcome, Form()]) -> RedirectResponse:
        try:
            intent = choose(transaction_id, outcome)
        except HTTPException as error:
            if error.status_code != 409:
                raise
            return RedirectResponse(f"/checkout/{transaction_id}", status_code=303)
        return RedirectResponse(intent.return_url, status_code=303)

    return app


app = create_app()
