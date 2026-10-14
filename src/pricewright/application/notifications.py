"""Emails (ADR-0028): what the outbox's consumers send, and the port that sends them.

A consumer decides who receives what and fills an ``Email`` with values; the mailer renders the
email's templates (text and HTML) with them and hands the message to the mail server.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Attachment:
    filename: str
    content: bytes
    media_type: str
    """``application/pdf``."""


@dataclass(frozen=True, slots=True)
class Email:
    message_id: str
    """The unique part of the ``Message-ID`` (the mailer adds the sender's domain): derived from
    the event behind the email, so a second delivery of the same event is recognizable as the
    same message (RFC 5322 §3.6.4)."""
    to: str
    subject: str
    template: str
    """The name of the email's text and HTML templates."""
    sender_name: str
    """Shown with the platform's sender address: the tenant's name."""
    values: Mapping[str, object] = field(default_factory=dict)
    reply_to: str | None = None
    attachments: tuple[Attachment, ...] = ()


class Mailer(Protocol):
    async def send(self, email: Email) -> None:
        """Hand the email to the mail server. Raise ``PermanentDeliveryError`` when the server
        refuses it for good (ADR-0008); any other error may pass with a retry."""
        ...
