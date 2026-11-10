"""Outgoing email behind a small interface (console in dev, capture in tests, SES/Resend later)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from .config import Settings

logger = logging.getLogger("attendify.email")


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class ConsoleEmailSender:
    async def send(self, message: EmailMessage) -> None:
        logger.info("EMAIL to=%s subject=%s\n%s", message.to, message.subject, message.body)


class CapturingEmailSender:
    """Keeps every message in memory so tests can read tokens out of them."""

    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.outbox.append(message)

    def to(self, address: str) -> list[EmailMessage]:
        return [m for m in self.outbox if m.to.lower() == address.lower()]


def build_email_sender(settings: Settings) -> EmailSender:
    if settings.email_backend == "capture":
        return CapturingEmailSender()
    return ConsoleEmailSender()


@dataclass
class Outbox:
    """Messages queued during a transaction and sent only after it commits, so an email never
    carries a token that was rolled back."""

    messages: list[EmailMessage] = field(default_factory=list)

    def add(self, to: str, subject: str, body: str) -> None:
        self.messages.append(EmailMessage(to, subject, body))

    async def flush(self, sender: EmailSender) -> None:
        pending, self.messages = self.messages, []
        for message in pending:
            try:
                await sender.send(message)
            except Exception:  # a mail outage must not fail the request that already committed
                logger.exception("Could not send email to %s", message.to)
