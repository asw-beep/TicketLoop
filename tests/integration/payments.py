"""Buying a ticket in tests: the calls a buyer's browser makes, and fakepay running in this process
so a test decides when it answers, what it says and when its webhooks arrive.

Import the fixture into a test module to use it:

    from tests.integration.payments import fakepay  # noqa: F401
"""

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import LOCAL_FAKEPAY_SECRET
from app.infrastructure.payments import FakepayGateway
from fakepay.config import FakepaySettings
from fakepay.main import create_app
from fakepay.webhooks import WebhookSender
from tests.integration.api import bearer
from tests.integration.conftest import new_account
from tests.integration.factories import make_listing

WEBHOOK_PATH = "/api/v1/payments/webhooks/fakepay"


class Fakepay:
    """fakepay on its own in-memory database, sending webhooks to `api` when told to."""

    def __init__(self, api: TestClient) -> None:
        self.now = time.time()
        self.down = False  # requests never reach it
        self.answers_lost = False  # requests reach it, answers do not come back
        self.calls: list[str] = []
        self.webhooks: list[httpx.Response] = []  # what the API answered
        self._due: list[Callable[[], None]] = []
        self._api = api
        settings = FakepaySettings(
            webhook_url=WEBHOOK_PATH, retry_waits=(), webhook_secret=LOCAL_FAKEPAY_SECRET
        )
        sender = WebhookSender(
            settings, post=self._post, later=lambda _, task: self._due.append(task)
        )
        self.client = TestClient(create_app(settings, sender, clock=lambda: self.now))

    def _post(self, url: str, body: bytes, headers: dict[str, str]) -> int:
        self.webhooks.append(self._api.post(url, content=body, headers=headers))
        return self.webhooks[-1].status_code

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Stands in for the API's HTTP client."""
        self.calls.append(f"{method} {path}")
        if self.down:
            raise httpx.ConnectError("fakepay is down")
        response = self.client.request(method, path, **kwargs)
        if self.answers_lost:
            raise httpx.ReadTimeout("the answer was lost")
        return response

    def close(self) -> None:
        pass

    def choose(self, transaction_id: str, outcome: str, *, deliver: bool = True) -> None:
        response = self.client.post(
            f"/v1/intents/{transaction_id}/outcome", json={"outcome": outcome}
        )
        assert response.status_code == 200, response.text
        if deliver:
            self.deliver()

    def deliver(self) -> None:
        due, self._due = self._due, []
        for task in due:
            task()


@pytest.fixture
def fakepay(db_app: FastAPI, api: TestClient) -> Iterator[Fakepay]:
    fake = Fakepay(api)
    db_app.state.payment_gateway = FakepayGateway(fake)  # type: ignore[arg-type]
    yield fake


# ---------------------------------------------------------------- the buyer's calls


def with_key(account: dict[str, str], key: str | None = None) -> dict[str, str]:
    return bearer(account["token"]) | {"Idempotency-Key": key or uuid.uuid4().hex}


def reserve(api: TestClient, listing_id: object, account: dict[str, str]) -> dict[str, Any]:
    response = api.post(
        f"/api/v1/listings/{listing_id}/reservations", headers=bearer(account["token"])
    )
    assert response.status_code == 201, response.text
    return response.json()


def post_order(
    api: TestClient, reservation_id: object, account: dict[str, str], key: str | None = None
) -> httpx.Response:
    return api.post(
        "/api/v1/orders",
        json={"reservation_id": str(reservation_id)},
        headers=with_key(account, key),
    )


def post_intent(
    api: TestClient, order_id: object, account: dict[str, str], key: str | None = None
) -> httpx.Response:
    return api.post(
        "/api/v1/payments/intents",
        json={"order_id": str(order_id)},
        headers=with_key(account, key),
    )


def held(api: TestClient, db: Session) -> tuple[dict[str, str], dict[str, Any]]:
    """(a buyer, the reservation they hold)"""
    buyer = new_account(api)
    return buyer, reserve(api, make_listing(db, status="ACTIVE").id, buyer)


def ordered(api: TestClient, db: Session) -> tuple[dict[str, str], dict[str, Any]]:
    """(a buyer, their unpaid order)"""
    buyer, reservation = held(api, db)
    response = post_order(api, reservation["id"], buyer)
    assert response.status_code == 201, response.text
    return buyer, response.json()


def paying(api: TestClient, db: Session) -> tuple[dict[str, str], dict[str, Any], dict[str, Any]]:
    """(a buyer, their order, the payment intent they were sent to)"""
    buyer, order = ordered(api, db)
    response = post_intent(api, order["id"], buyer)
    assert response.status_code == 201, response.text
    return buyer, order, response.json()


# ---------------------------------------------------------------- hand-made webhooks


def event(intent: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {
        "event_id": f"evt_{uuid.uuid4().hex}",
        "type": "payment.succeeded",
        "transaction_id": intent["provider_transaction_id"],
        "amount_paise": intent["amount_paise"],
        "currency": "INR",
        "occurred_at": "2026-11-26T10:00:00Z",
    } | fields


def signed(
    body: dict[str, Any] | bytes,
    *,
    secret: str = LOCAL_FAKEPAY_SECRET,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """The body and headers of a webhook, for any client's post()."""
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    stamp = timestamp if timestamp is not None else str(int(time.time()))
    signature = hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    headers = {
        "X-Fakepay-Timestamp": stamp,
        "X-Fakepay-Signature": signature,
        "Content-Type": "application/json",
    }
    return {"content": raw, "headers": headers}


def post_webhook(
    api: TestClient,
    body: dict[str, Any] | bytes,
    *,
    headers: dict[str, str] | None = None,
    **how: Any,
) -> httpx.Response:
    request = signed(body, **how)
    return api.post(WEBHOOK_PATH, **request | {"headers": request["headers"] | (headers or {})})
