"""Sending email. A port: code depends on EmailSender, and the implementation can change.

Only the in-memory sender exists so far. It keeps messages in a list that tests read, and it
never prints a message body, because bodies carry secrets such as password-reset tokens.
A real sender (SMTP, or a provider API) can replace it in create_app without touching the
code that sends; from M11 it would run from the outbox, after the transaction commits.
"""

from collections import deque
from dataclasses import dataclass
from typing import Protocol

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> None: ...


class InMemoryEmailSender:
    """Keeps the most recent messages; nothing leaves the process."""

    def __init__(self, keep: int = 100) -> None:
        self._sent: deque[EmailMessage] = deque(maxlen=keep)

    @property
    def sent(self) -> list[EmailMessage]:
        return list(self._sent)

    def send(self, message: EmailMessage) -> None:
        self._sent.append(message)
        log.info("email.sent", subject=message.subject)  # no address and no body: PII, secrets
