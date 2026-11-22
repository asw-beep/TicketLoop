"""Sending email. A port: code depends on EmailSender, and the implementation can change.

Two senders. Tests use the in-memory one and read its list. A laptop uses SMTP to Mailpit
(docker compose), which catches every message and shows it at http://localhost:8025 instead
of delivering it. create_app picks one from Settings.smtp_server. Neither logs a message
body, because bodies carry secrets such as password-reset links. From M11 sending moves to
the outbox, after the transaction commits.
"""

import smtplib
from collections import deque
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
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


class SmtpEmailSender:
    """Sends each message over a fresh SMTP connection.

    Plain SMTP, no TLS or login: Mailpit needs neither. A real provider would add STARTTLS
    and credentials when the app is deployed (M15).
    """

    def __init__(self, *, host: str, port: int, sender: str, timeout_seconds: float = 5) -> None:
        self._host, self._port, self._sender = host, port, sender
        self._timeout = timeout_seconds

    def send(self, message: EmailMessage) -> None:
        mime = MimeMessage()  # refuses a header holding a line break: no injected headers
        mime["From"] = self._sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.body)
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
            smtp.send_message(mime)
        log.info("email.sent", subject=message.subject)
