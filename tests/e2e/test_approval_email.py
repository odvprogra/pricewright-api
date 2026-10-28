"""Brief §7, end to end: "a 20% discount triggers approval; the manager approves from the inbox; the
rep receives an email with the PDF" (ADR-0008, ADR-0027, ADR-0028).

The API runs on the fakes; the worker's relay then delivers the outbox to the quote emails.
"""

import httpx

from pricewright.api.app import create_app
from pricewright.application.outbox import relay_events
from pricewright.application.ports import UnitOfWork
from pricewright.application.quote_emails import QuoteEmails
from pricewright.domain.users import Role, User
from tests.e2e.test_quote_approvals import GENEROUS_VOLUME, INBOX, act
from tests.e2e.test_quotes import ACME, BOLTS, NORTHFIELD, PATH, bearer, new_quote
from tests.fakes import (
    FakeClock,
    FakeMailer,
    FakeQuoteDocuments,
    FakeUnitOfWork,
    InMemoryDatabase,
    fake_services,
)


def person(email: str, name: str, role: Role) -> User:
    return User.create(
        tenant_id=NORTHFIELD.id,
        email=email,
        full_name=name,
        role=role,
        password_hash="hashed:secret",
    )


async def test_the_rep_receives_the_approved_quote_s_pdf_by_email() -> None:
    jordan = person("jordan@northfield.example", "Jordan Rep", Role.SALES_REP)
    morgan = person("morgan@northfield.example", "Morgan Manager", Role.SALES_MANAGER)
    database = InMemoryDatabase(
        tenants={NORTHFIELD.id: NORTHFIELD},
        users={user.id: user for user in (jordan, morgan)},
        products={BOLTS.id: BOLTS},
        customers={ACME.id: ACME},
        pricing_rules={GENEROUS_VOLUME.id: GENEROUS_VOLUME},
    )
    clock = FakeClock()
    app = create_app(title="test", services=fake_services(database, clock))
    rep, manager = bearer(Role.SALES_REP, jordan.id), bearer(Role.SALES_MANAGER, morgan.id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        draft = await client.post(PATH, json=new_quote(), headers=rep)
        submitted = await act(client, draft, "submit", rep)
        [request] = (await client.get(INBOX, headers=manager)).json()["items"]
        quote = await client.get(f"{PATH}/{request['quote']['id']}", headers=manager)
        approved = await act(client, quote, "approve", manager)

    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(database)

    mailer, documents = FakeMailer(), FakeQuoteDocuments()
    emails = QuoteEmails(unit_of_work=unit_of_work, documents=documents, mailer=mailer, clock=clock)
    await relay_events(unit_of_work=unit_of_work, consumers=emails.consumers(), clock=clock)

    assert submitted.json()["discount"] == "0.2000"
    assert submitted.json()["status"] == "pending_approval"
    assert approved.json()["status"] == "approved"
    [email] = mailer.sent
    assert (email.to, email.subject) == (
        "jordan@northfield.example",
        "Quote NF-2026-000001 approved",
    )
    assert email.values["decider"] == "Morgan Manager"
    [pdf] = email.attachments
    assert (pdf.filename, pdf.media_type) == ("NF-2026-000001.pdf", "application/pdf")
    assert pdf.content == b"%PDF-fake NF-2026-000001 approved"
