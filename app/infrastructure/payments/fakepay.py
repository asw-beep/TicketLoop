"""The client for fakepay, the pretend gateway in fakepay/."""

import httpx2 as httpx
from pydantic import BaseModel, ValidationError

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
        return self._look_up(f"/v1/intents/{transaction_id}")

    def by_idempotency_key(self, idempotency_key: str) -> GatewayIntent | None:
        return self._look_up("/v1/intents", idempotency_key=idempotency_key)

    def _look_up(self, path: str, **params: str) -> GatewayIntent | None:
        response = self._send("GET", path, params=params)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise GatewayUnavailable(f"fakepay answered {response.status_code} to a lookup")
        return _parse(response)

    def _send(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as error:  # timeouts and broken connections included
            raise GatewayUnavailable(f"fakepay did not answer: {error!r}") from error
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
