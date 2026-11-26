"""Signed webhooks, sent in the background and retried, the way a real gateway does it."""

import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import httpx2 as httpx

from fakepay.config import FakepaySettings
from fakepay.store import Intent

log = logging.getLogger("fakepay")

# (url, body, headers) -> the status code the receiver answered with.
Post = Callable[[str, bytes, dict[str, str]], int]
# Run this, this many seconds from now, without making anyone wait for it.
Later = Callable[[float, Callable[[], None]], None]


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def post_over_http(url: str, body: bytes, headers: dict[str, str]) -> int:
    return httpx.post(url, content=body, headers=headers, timeout=10).status_code


def later_on_a_thread(seconds: float, task: Callable[[], None]) -> None:
    timer = threading.Timer(seconds, task)
    timer.daemon = True
    timer.start()


class WebhookSender:
    def __init__(
        self,
        settings: FakepaySettings,
        post: Post = post_over_http,
        later: Later = later_on_a_thread,
    ) -> None:
        self._settings = settings
        self._post = post
        self._later = later

    def event(self, intent: Intent) -> bytes:
        """One event's body. Delivered again, it is these same bytes with this same event_id."""
        return json.dumps(
            {
                "event_id": f"evt_{uuid.uuid4().hex}",
                "type": f"payment.{intent.settled}",
                "transaction_id": intent.transaction_id,
                "amount_paise": intent.amount_paise,
                "currency": intent.currency,
                "occurred_at": datetime.now(UTC).isoformat(),
            }
        ).encode()

    def send(self, body: bytes, *, after: float = 0.0, forged: bool = False) -> None:
        waits = () if forged else self._settings.retry_waits
        self._later(after, lambda: self._attempt(body, waits, forged))

    def _attempt(self, body: bytes, waits: tuple[float, ...], forged: bool) -> None:
        timestamp = str(int(time.time()))  # signed anew on every try, as it must be recent
        secret = "not-the-secret" if forged else self._settings.webhook_secret.get_secret_value()
        headers = {
            "Content-Type": "application/json",
            "X-Fakepay-Timestamp": timestamp,
            "X-Fakepay-Signature": sign(secret, timestamp, body),
        }
        try:
            status = self._post(self._settings.webhook_url, body, headers)
        except (OSError, httpx.HTTPError) as error:
            log.warning("webhook not delivered: %s", error)
            status = 0
        if 200 <= status < 300 or not waits:
            return
        self._later(waits[0], lambda: self._attempt(body, waits[1:], forged))
