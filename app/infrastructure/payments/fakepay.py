"""The client for fakepay, the pretend gateway in fakepay/.

Each call is a CLIENT span named by its route, never by an id in its path, and carries the
trace on in a `traceparent` header.
"""

from collections.abc import Mapping
from typing import Any

import httpx2 as httpx
from pydantic import BaseModel, ValidationError

from app.core import telemetry
from app.infrastructure.payments.base import (
    GatewayIntent,
    GatewayRefused,
    GatewayUnavailable,
    IntentStatus,
)

TIMEOUT_SECONDS = 5.0
# 4xx answers that say "not now" rather than "no".
_TRY_LATER = {408, 425, 429}
# 4xx answers about how we asked (a wrong key, a wrong address), not about the payment.
_NOT_ABOUT_THE_PAYMENT = {401, 403, 404, 405}


class _Intent(BaseModel):
    transaction_id: str
    status: IntentStatus
    amount_paise: int
    currency: str
    checkout_url: str


class FakepayGateway:
    name = "fakepay"

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    @classmethod
    def at(cls, url: str) -> "FakepayGateway":
        # A plain-http gateway has no certificate, and loading the CA bundle takes a tenth
        # of a second that every test app would pay.
        verify = not url.startswith("http://")
        return cls(httpx.Client(base_url=url, timeout=TIMEOUT_SECONDS, verify=verify))

    def close(self) -> None:
        self._client.close()

    def create_intent(
        self, *, idempotency_key: str, amount_paise: int, currency: str, return_url: str
    ) -> GatewayIntent:
        response = self._send(
            "POST",
            "/v1/intents",
            headers={"Idempotency-Key": idempotency_key},
            json={
                "amount_paise": amount_paise,
                "currency": currency,
                "return_url": return_url,
            },
        )
        if response.status_code in _NOT_ABOUT_THE_PAYMENT:
            raise GatewayUnavailable(f"fakepay answered {response.status_code} to a new intent")
        if response.status_code >= 400:
            raise GatewayRefused(f"fakepay answered {response.status_code}: {response.text[:200]}")
        return _parse(response)

    def by_transaction(self, transaction_id: str) -> GatewayIntent | None:
        return self._look_up(f"/v1/intents/{transaction_id}", route="/v1/intents/{transaction_id}")

    def by_idempotency_key(self, idempotency_key: str) -> GatewayIntent | None:
        return self._look_up("/v1/intents", idempotency_key=idempotency_key)

    def _look_up(self, path: str, route: str | None = None, **params: str) -> GatewayIntent | None:
        response = self._send("GET", path, route=route, params=params)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise GatewayUnavailable(f"fakepay answered {response.status_code} to a lookup")
        return _parse(response)

    def _send(
        self,
        method: str,
        path: str,
        *,
        route: str | None = None,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """`route` is the path as a template, when the path has an id in it."""
        attributes = {"http.request.method": method, "url.template": route or path}
        with telemetry.client_span(f"fakepay {method} {route or path}", attributes) as span:
            sent = {**(headers or {}), **telemetry.current_context_headers()}
            try:
                response = self._client.request(method, path, headers=sent, **kwargs)
            except httpx.HTTPError as error:  # timeouts and broken connections included
                raise GatewayUnavailable(f"fakepay did not answer: {error!r}") from error
            span.set_attribute("http.response.status_code", response.status_code)
            if response.status_code >= 500 or response.status_code in _TRY_LATER:
                raise GatewayUnavailable(f"fakepay answered {response.status_code}")
            return response


def _parse(response: httpx.Response) -> GatewayIntent:
    try:
        snapshot = response.json()
        intent = _Intent.model_validate(snapshot)
    except (ValueError, ValidationError) as error:
        raise GatewayUnavailable("fakepay's answer made no sense") from error
    return GatewayIntent(snapshot=snapshot, **intent.model_dump())
