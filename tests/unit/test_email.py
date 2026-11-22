"""Where email goes: app/infrastructure/external_providers/email.py and Settings.smtp_server."""

import email
import email.policy
import socket
from collections.abc import Iterator

import pytest
from aiosmtpd.controller import Controller

from app.core.config import Settings
from app.infrastructure.external_providers.email import (
    EmailMessage,
    InMemoryEmailSender,
    SmtpEmailSender,
)
from app.main import create_app


class Inbox:
    """An aiosmtpd handler that keeps every message it receives."""

    def __init__(self) -> None:
        self.envelopes = []

    async def handle_DATA(self, server, session, envelope) -> str:  # noqa: N802 (aiosmtpd's name)
        self.envelopes.append(envelope)
        return "250 Message accepted"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def smtp_server() -> Iterator[tuple[Inbox, int]]:
    """A real SMTP server on localhost, as Mailpit is, for the length of one test."""
    inbox, port = Inbox(), free_port()
    controller = Controller(inbox, hostname="127.0.0.1", port=port)
    controller.start()
    yield inbox, port
    controller.stop()


def test_the_smtp_sender_delivers_a_plain_text_message(smtp_server: tuple[Inbox, int]) -> None:
    inbox, port = smtp_server
    sender = SmtpEmailSender(
        host="127.0.0.1", port=port, sender="TicketLoop <no-reply@ticketloop.local>"
    )

    sender.send(
        EmailMessage(to="asha@example.in", subject="Reset", body="Choose: http://x/#token=t")
    )

    [envelope] = inbox.envelopes
    assert envelope.rcpt_tos == ["asha@example.in"]
    received = email.message_from_bytes(envelope.content, policy=email.policy.default)
    assert (received["From"], received["To"], received["Subject"]) == (
        "TicketLoop <no-reply@ticketloop.local>",
        "asha@example.in",
        "Reset",
    )
    assert received.get_content().strip() == "Choose: http://x/#token=t"


def test_the_smtp_sender_refuses_a_header_with_a_line_break() -> None:
    # A line break in a header would let the text after it add headers of its own, such as
    # a Bcc. The message is refused before any connection is made.
    sender = SmtpEmailSender(host="127.0.0.1", port=free_port(), sender="a@example.in")

    with pytest.raises(ValueError):
        sender.send(EmailMessage(to="a@example.in", subject="Hi\nBcc: x@evil.example", body="."))


@pytest.mark.parametrize(
    ("fields", "server"),
    [
        ({"environment": "local"}, ("localhost", 1025)),  # Mailpit from docker compose
        ({"environment": "test"}, None),  # the in-memory sender the tests read
        (
            {"environment": "test", "smtp_host": "mail.internal", "smtp_port": 2525},
            ("mail.internal", 2525),
        ),
    ],
    ids=["laptop", "tests", "explicit"],
)
def test_where_email_goes_by_default(fields: dict, server: tuple[str, int] | None) -> None:
    assert Settings(_env_file=None, **fields).smtp_server == server


def test_the_app_sends_over_smtp_only_when_a_server_is_configured() -> None:
    in_tests = create_app(Settings(_env_file=None, environment="test"))
    on_a_laptop = create_app(Settings(_env_file=None, environment="local"))

    assert isinstance(in_tests.state.email_sender, InMemoryEmailSender)
    assert isinstance(on_a_laptop.state.email_sender, SmtpEmailSender)
