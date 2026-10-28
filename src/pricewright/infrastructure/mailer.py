"""Emails over SMTP (ADR-0028): the standard library's client, run in a thread.

The mailer renders an email's text and HTML templates (``templates/email/<name>.txt`` and
``.html``), builds the MIME message and hands it to the mail server: Mailpit locally, a relay such
as SES or Postmark when deployed. A server's permanent refusal (a 5xx reply) is a
``PermanentDeliveryError``, so the outbox gives up on the event at once (ADR-0008).
"""

import asyncio
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate

from jinja2 import Environment

from pricewright.application.notifications import Email
from pricewright.application.outbox import PermanentDeliveryError
from pricewright.infrastructure.documents import environment


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    host: str
    port: int
    sender: str
    """The platform's address: emails leave on the tenant's behalf, replies go to people."""
    username: str | None = None
    password: str | None = None
    starttls: bool = False
    timeout: float = 10


def _permanent(code: int) -> bool:
    return 500 <= code < 600  # RFC 5321 §4.2.1: 5yz is a permanent negative completion reply


class SmtpMailer:
    def __init__(self, settings: SmtpSettings, templates: Environment | None = None) -> None:
        self._settings = settings
        self._templates = templates or environment()

    async def send(self, email: Email) -> None:
        await asyncio.to_thread(self._send, self.message(email))

    def message(self, email: Email) -> EmailMessage:
        render = self._templates.get_template
        message = EmailMessage()
        message["From"] = formataddr((email.sender_name, self._settings.sender))
        message["To"] = email.to
        if email.reply_to is not None:
            message["Reply-To"] = email.reply_to
        message["Subject"] = email.subject
        message["Date"] = formatdate(usegmt=True)
        domain = self._settings.sender.rpartition("@")[2]
        message["Message-ID"] = f"<{email.message_id}@{domain}>"
        message.set_content(render(f"email/{email.template}.txt").render(email.values))
        html = render(f"email/{email.template}.html").render(email.values)
        message.add_alternative(html, subtype="html")
        for attachment in email.attachments:
            maintype, _, subtype = attachment.media_type.partition("/")
            message.add_attachment(
                attachment.content, maintype, subtype, filename=attachment.filename
            )
        return message

    def _send(self, message: EmailMessage) -> None:
        settings = self._settings
        try:
            with smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout) as smtp:
                if settings.starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if settings.username is not None and settings.password is not None:
                    smtp.login(settings.username, settings.password)
                smtp.send_message(message)
        except smtplib.SMTPRecipientsRefused as error:
            codes = [code for code, _ in error.recipients.values()]
            if all(_permanent(code) for code in codes):
                raise PermanentDeliveryError(
                    f"the mail server refused the recipient ({codes})"
                ) from error
            raise
        except smtplib.SMTPResponseException as error:
            if _permanent(error.smtp_code):
                raise PermanentDeliveryError(
                    f"the mail server refused the email ({error.smtp_code})"
                ) from error
            raise
