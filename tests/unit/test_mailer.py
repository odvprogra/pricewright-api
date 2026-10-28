"""The SMTP mailer without a mail server (ADR-0028): the message it builds, and how it reads the
server's refusals. A real send to Mailpit is in tests/integration/test_mailer.py."""

import dataclasses
import smtplib
import ssl
from email.message import EmailMessage
from types import TracebackType
from typing import Self

import pytest
from jinja2 import DictLoader, Environment, StrictUndefined, select_autoescape

from pricewright.application.notifications import Attachment, Email
from pricewright.application.outbox import PermanentDeliveryError
from pricewright.infrastructure.mailer import SmtpMailer, SmtpSettings

TEMPLATES = Environment(
    loader=DictLoader(
        {
            "email/hello.txt": "Hello {{ name }}, your quote is attached.\n",
            "email/hello.html": "<p>Hello {{ name }}, your quote is attached.</p>\n",
        }
    ),
    autoescape=select_autoescape(["html"]),
    undefined=StrictUndefined,
)
SETTINGS = SmtpSettings(host="mail.example", port=2525, sender="quotes@pricewright.example")
EMAIL = Email(
    message_id="0199a1b2-event",
    to="purchasing@acme.example",
    subject="Quotation NF-2026-000001",
    template="hello",
    sender_name="Northfield Supply",
    values={"name": "Acme & Sons"},
    reply_to="jordan@northfield.example",
    attachments=(Attachment("NF-2026-000001.pdf", b"%PDF-1.7 quote", "application/pdf"),),
)


def body(message: EmailMessage, subtype: str) -> str:
    part = message.get_body((subtype,))
    assert part is not None
    content: str = part.get_content()
    return content


def test_mailer_writes_the_message_on_the_tenant_s_behalf() -> None:
    message = SmtpMailer(SETTINGS, TEMPLATES).message(EMAIL)

    assert message["From"] == "Northfield Supply <quotes@pricewright.example>"
    assert (message["To"], message["Reply-To"]) == (EMAIL.to, EMAIL.reply_to)
    assert message["Subject"] == EMAIL.subject
    assert message["Message-ID"] == "<0199a1b2-event@pricewright.example>"
    assert message["Date"].endswith("+0000")  # UTC
    assert body(message, "plain") == "Hello Acme & Sons, your quote is attached.\n"
    assert body(message, "html") == "<p>Hello Acme &amp; Sons, your quote is attached.</p>\n"
    [pdf] = message.iter_attachments()
    assert (pdf.get_filename(), pdf.get_content_type()) == ("NF-2026-000001.pdf", "application/pdf")
    assert pdf.get_content() == b"%PDF-1.7 quote"


def test_mailer_leaves_reply_to_out_when_nobody_takes_replies() -> None:
    plain = Email("approved-1", "rep@northfield.example", "Approved", "hello", "Northfield")
    plain = dataclasses.replace(plain, values={"name": "Jordan"})

    message = SmtpMailer(SETTINGS, TEMPLATES).message(plain)

    assert message["Reply-To"] is None
    assert list(message.iter_attachments()) == []


class FakeSmtp:
    """Stands in for ``smtplib.SMTP``: records what the mailer asks, fails as told."""

    def __init__(self, failure: Exception | None) -> None:
        self.calls: list[str] = []
        self._failure = failure

    def __call__(self, host: str, port: int, *, timeout: float) -> Self:
        self.calls.append(f"connect {host}:{port} in {timeout:g}s")
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.calls.append("quit")

    def starttls(self, *, context: ssl.SSLContext) -> None:
        assert context.verify_mode is ssl.CERT_REQUIRED
        self.calls.append("starttls")

    def login(self, user: str, _password: str) -> None:
        self.calls.append(f"login {user}")

    def send_message(self, message: EmailMessage) -> None:
        if self._failure is not None:
            raise self._failure
        self.calls.append(f"send to {message['To']}")


def fake_smtp(monkeypatch: pytest.MonkeyPatch, failure: Exception | None = None) -> FakeSmtp:
    smtp = FakeSmtp(failure)
    monkeypatch.setattr(smtplib, "SMTP", smtp)
    return smtp


async def test_mailer_uses_starttls_and_credentials_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smtp = fake_smtp(monkeypatch)
    secured = SmtpSettings(
        host="smtp.example",
        port=587,
        sender="quotes@pricewright.example",
        username="apikey",
        password="from-the-environment",
        starttls=True,
        timeout=5,
    )

    await SmtpMailer(secured, TEMPLATES).send(EMAIL)

    assert smtp.calls == [
        "connect smtp.example:587 in 5s",
        "starttls",
        "login apikey",
        "send to purchasing@acme.example",
        "quit",
    ]


@pytest.mark.parametrize(
    "refusal",
    [
        smtplib.SMTPRecipientsRefused({"x@acme.example": (550, b"5.1.1 no such mailbox")}),
        smtplib.SMTPSenderRefused(553, b"5.7.1 sender rejected", "quotes@pricewright.example"),
        smtplib.SMTPDataError(554, b"5.7.1 message rejected"),
    ],
    ids=["no-such-mailbox", "sender-refused", "message-refused"],
)
async def test_mailer_reads_a_5xx_refusal_as_permanent(
    monkeypatch: pytest.MonkeyPatch, refusal: Exception
) -> None:
    fake_smtp(monkeypatch, refusal)

    with pytest.raises(PermanentDeliveryError, match="refused"):
        await SmtpMailer(SETTINGS, TEMPLATES).send(EMAIL)


@pytest.mark.parametrize(
    "failure",
    [
        smtplib.SMTPRecipientsRefused({"x@acme.example": (450, b"4.2.1 mailbox busy")}),
        smtplib.SMTPDataError(451, b"4.3.0 try again later"),
        ConnectionRefusedError("the mail server is down"),
    ],
    ids=["mailbox-busy", "try-later", "server-down"],
)
async def test_mailer_lets_a_temporary_failure_through_for_a_retry(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    fake_smtp(monkeypatch, failure)

    with pytest.raises(type(failure)):
        await SmtpMailer(SETTINGS, TEMPLATES).send(EMAIL)
