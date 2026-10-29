"""The quote emails (ADR-0028): who receives what when the relay delivers quote events, on the
fakes; each email is also rendered with the real templates."""

import uuid

import pytest

from pricewright.application.outbox import relay_events
from pricewright.application.ports import UnitOfWork
from pricewright.application.quote_emails import QuoteEmails
from pricewright.application.quotes import reject_quote, send_quote
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.events import EventType
from pricewright.domain.users import Role, User
from pricewright.infrastructure.mailer import SmtpMailer, SmtpSettings
from tests.fakes import FakeMailer, FakeQuoteDocuments, FakeUnitOfWork
from tests.unit.test_quote_approval_management import approve
from tests.unit.test_quote_management import Fixture
from tests.unit.test_quote_transition_management import generous, submit

RENDERER = SmtpMailer(SmtpSettings(host="unused", port=25, sender="quotes@pricewright.example"))


class Office:
    """Northfield with people who have mailboxes, and the relay's consumers."""

    def __init__(self) -> None:
        self.f = Fixture()
        tenant = self.f.northfield.id
        self.jordan = self.person("jordan@northfield.example", "Jordan Rep", Role.SALES_REP)
        self.morgan = self.person("morgan@northfield.example", "Morgan Manager", Role.SALES_MANAGER)
        self.rep = Principal(tenant, self.jordan.id, Role.SALES_REP)
        self.manager = Principal(tenant, self.morgan.id, Role.SALES_MANAGER)
        self.mailer, self.documents = FakeMailer(), FakeQuoteDocuments()

    def person(self, email: str, name: str, role: Role) -> User:
        user = User.create(
            tenant_id=self.f.northfield.id,
            email=email,
            full_name=name,
            role=role,
            password_hash="hashed:secret",
        )
        self.f.database.users[user.id] = user
        return user

    async def relay(self) -> None:
        def unit_of_work() -> UnitOfWork:
            return FakeUnitOfWork(self.f.database)

        emails = QuoteEmails(
            unit_of_work=unit_of_work,
            documents=self.documents,
            mailer=self.mailer,
            clock=self.f.clock,
        )
        await relay_events(
            unit_of_work=unit_of_work, consumers=emails.consumers(), clock=self.f.clock
        )

    def event_id(self, kind: EventType) -> str:
        [stored] = [s for s in self.f.database.outbox_events.values() if s.event.type is kind]
        return str(stored.event.id)


@pytest.fixture
def office() -> Office:
    return Office()


async def test_an_approval_emails_the_rep_who_submitted_it_with_the_pdf(office: Office) -> None:
    quote = await submit(office.f, await generous(office.f), office.rep)
    await approve(office.f, quote, office.manager, comment="Fine for a gold account.")

    await office.relay()

    [email] = office.mailer.sent
    assert (email.to, email.subject) == (
        "jordan@northfield.example",
        "Quote NF-2026-000001 approved",
    )
    assert (email.template, email.sender_name, email.reply_to) == (
        "quote_approved",
        "Northfield",
        None,
    )
    assert email.message_id == office.event_id(EventType.QUOTE_APPROVED)
    assert email.values["decider"] == "Morgan Manager"
    assert email.values["comment"] == "Fine for a gold account."
    [pdf] = email.attachments
    assert (pdf.filename, pdf.media_type) == ("NF-2026-000001.pdf", "application/pdf")
    assert pdf.content == b"%PDF-fake NF-2026-000001 approved"
    text = RENDERER.message(email).get_body(("plain",))
    assert text is not None
    assert "Morgan Manager approved your quote for Acme." in text.get_content()
    assert "Quote: NF-2026-000001" in text.get_content()


async def test_a_rejection_emails_the_rep_what_to_change_without_a_pdf(office: Office) -> None:
    quote = await submit(office.f, await generous(office.f), office.rep)
    await reject_quote(
        office.manager,
        quote.id,
        "Keep the bolts above their margin floor.",
        expected_version=office.f.database.quotes[quote.id].version,
        unit_of_work=office.f.unit_of_work,
        clock=office.f.clock,
    )

    await office.relay()

    [email] = office.mailer.sent
    assert (email.to, email.subject) == (
        "jordan@northfield.example",
        "Quote NF-2026-000001 needs changes",
    )
    assert (email.template, email.attachments) == ("quote_rejected", ())
    html = RENDERER.message(email).get_body(("html",))
    assert html is not None
    assert "Keep the bolts above their margin floor." in html.get_content()


async def send(office: Office) -> str:
    quote = await submit(office.f, await office.f.create(), office.rep)
    await send_quote(
        office.rep,
        quote.id,
        expected_version=office.f.database.quotes[quote.id].version,
        unit_of_work=office.f.unit_of_work,
        clock=office.f.clock,
    )
    return quote.display_number


async def test_a_sent_quote_reaches_the_customer_with_replies_to_the_rep(office: Office) -> None:
    acme = office.f.acme
    acme.email = "purchasing@acme.example"

    number = await send(office)
    await office.relay()

    [email] = office.mailer.sent
    assert (email.to, email.reply_to) == ("purchasing@acme.example", "jordan@northfield.example")
    assert email.subject == f"Quotation {number} from Northfield"
    assert email.template == "quote_sent"
    [pdf] = email.attachments
    assert pdf.content == b"%PDF-fake NF-2026-000001 sent"
    text = RENDERER.message(email).get_body(("plain",))
    assert text is not None
    assert "Jordan Rep at Northfield sends you a quotation for Acme." in text.get_content()
    assert f"Quotation: {number}" in text.get_content()


async def test_a_customer_without_an_email_gets_nothing_and_the_event_is_done(
    office: Office,
) -> None:
    await send(office)

    await office.relay()

    assert office.mailer.sent == []
    assert {s.status.value for s in office.f.database.outbox_events.values()} == {"delivered"}


async def test_decisions_on_an_integration_s_quote_email_no_one(office: Office) -> None:
    integration = Principal(
        office.f.northfield.id, uuid.uuid7(), scopes=frozenset({Permission.QUOTES_MANAGE})
    )
    quote = await submit(office.f, await generous(office.f), integration)
    await approve(office.f, quote, office.manager)

    await office.relay()

    assert office.mailer.sent == []
