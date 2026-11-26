"""What the app needs from a payment gateway, whichever one it is."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

IntentStatus = Literal["open", "succeeded", "failed", "expired"]


class GatewayUnavailable(Exception):
    """No usable answer: it timed out, could not be reached, failed, or made no sense. Nothing
    may be concluded from this; in particular, not that no money moved."""


class GatewayRefused(Exception):
    """The gateway understood and said no. It made no payment intent."""


@dataclass(frozen=True)
class GatewayIntent:
    transaction_id: str
    status: IntentStatus
    amount_paise: int
    currency: str
    checkout_url: str
    snapshot: dict[str, Any]  # the gateway's own words, kept on the payment


class PaymentGateway(Protocol):
    name: str

    def create_intent(
        self, *, idempotency_key: str, amount_paise: int, currency: str, return_url: str
    ) -> GatewayIntent:
        """The same idempotency_key always gets the same intent."""
        ...

    def by_transaction(self, transaction_id: str) -> GatewayIntent | None: ...

    def by_idempotency_key(self, idempotency_key: str) -> GatewayIntent | None: ...
