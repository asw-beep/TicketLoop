"""The ticket provider: whoever can put a ticket in a new holder's name (the event's box office).

A port, like the email sender: transfers depend on TicketProvider, and the in-process fake is
the only implementation, because the slimmed roadmap cut the separate provider service. It has
switches for each way a real provider fails, which the tests and the drills turn on.

Every call carries an idempotency key, the transfer's id. Asking twice with the same key is
safe: a real provider does the transfer once and gives the same answer both times, and so does
the fake. That is what lets a worker that died mid-call simply ask again.
"""

import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Literal, Protocol

ProviderMode = Literal["succeed", "retryable", "permanent", "unknown"]


class TransferUnavailable(Exception):
    """Try again later: the provider is down, busy or timed out before starting."""


class TransferRefused(Exception):
    """It will never work: the ticket is void, or not this holder's to give."""


class TransferOutcomeUnknown(Exception):
    """The provider may or may not have done it, and cannot say. Never retried blindly."""


@dataclass(frozen=True)
class TransferRequest:
    idempotency_key: str
    provider_reference: str  # the provider's own id for the ticket
    to_user_id: uuid.UUID


class TicketProvider(Protocol):
    def transfer(self, request: TransferRequest) -> str:
        """Move the ticket; return the provider's reference for the transfer."""
        ...


class FakeTicketProvider:
    """Succeeds by default. `mode` picks a failure; with mode "retryable", `failures` is how
    many tries per key fail before one succeeds (0: every one fails). `delay_seconds` makes
    each call slow, so a drill can kill a worker in the middle of one."""

    def __init__(
        self, mode: ProviderMode = "succeed", failures: int = 0, delay_seconds: float = 0.0
    ) -> None:
        self.mode = mode
        self.failures = failures
        self.delay_seconds = delay_seconds
        self.calls: Counter[str] = Counter()
        self._lock = threading.Lock()

    def transfer(self, request: TransferRequest) -> str:
        with self._lock:
            self.calls[request.idempotency_key] += 1
            tries = self.calls[request.idempotency_key]
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        match self.mode:
            case "retryable" if self.failures == 0 or tries <= self.failures:
                raise TransferUnavailable(f"provider busy (try {tries})")
            case "permanent":
                raise TransferRefused("the provider refused: ticket not transferable")
            case "unknown":
                raise TransferOutcomeUnknown("the provider timed out mid-transfer")
        # The same key always gets the same reference: asking again is harmless.
        return f"fpv_{request.idempotency_key.replace('-', '')}"
