"""Quote documents (ADR-0027): what the PDF shows, rendered for real with WeasyPrint and read back
with pypdf, the pinned design tokens it is styled with (D-61), and the use case behind
``GET /quotes/{id}/pdf``."""

import io
import json
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import cast

import pytest
from pypdf import PdfReader
from pypdf.generic import DictionaryObject

from pricewright.application.documents import QuoteDocument, render_quote
from pricewright.application.ports import UnitOfWork
from pricewright.domain.actors import Actor
from pricewright.domain.audit import ActorType
from pricewright.domain.auth import Permission, PermissionDeniedError, Principal
from pricewright.domain.errors import NotFoundError
from pricewright.domain.money import Money
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.quotes import LineChange, Quote
from pricewright.domain.tenants import Tenant
from pricewright.domain.users import Role, User
from pricewright.infrastructure.documents import (
    BRAND,
    DESIGN_RELEASE,
    TEMPLATES,
    WeasyPrintQuoteDocuments,
    amount,
    discount,
    first_name,
    long_date,
    money,
    net_price,
    percent,
    quantity,
    sentence_end,
    unit_price,
)
from tests.fakes import FakeClock, FakeQuoteDocuments, FakeUnitOfWork, InMemoryDatabase
from tests.unit.quote_data import ACME, BOLTS, GLOVES, NOW, REP, SETTINGS, context, draft, usd

NORTHFIELD = Tenant(id=ACME.tenant_id, name="Northfield Supply", settings=SETTINGS)


def quoted() -> Quote:
    quote = draft(
        LineChange(BOLTS.id, Decimal(12)),
        LineChange(GLOVES.id, Decimal(3)),
    )
    quote.notes = "Delivery within two weeks."
    return quote


def document_of(quote: Quote, status: QuoteStatus | None = None) -> QuoteDocument:
    return QuoteDocument(
        tenant=NORTHFIELD,
        quote=quote,
        customer=ACME,
        prepared_by="Jordan Rep",
        status=status or quote.status,
        now=NOW,
    )


def text_of(pdf: bytes) -> str:
    """The PDF's text with its whitespace collapsed: where lines break depends on the fonts."""
    pages = PdfReader(io.BytesIO(pdf)).pages
    return " ".join(" ".join(page.extract_text() for page in pages).split())


def fonts_of(pdf: bytes) -> set[str]:
    """The fonts embedded in the PDF, without their subset prefix (``ABCDEF+Public-Sans``)."""
    names = set()
    for page in PdfReader(io.BytesIO(pdf)).pages:
        fonts = cast(DictionaryObject, cast(DictionaryObject, page["/Resources"])["/Font"])
        for font in fonts.values():
            name = cast(DictionaryObject, font.get_object())["/BaseFont"]
            names.add(str(name).split("+", 1)[-1])
    return names


async def test_quote_pdf_shows_the_offer_and_never_its_costs() -> None:
    quote = quoted()
    quote.submit(by=REP, context=context())

    text = text_of(await WeasyPrintQuoteDocuments().render(document_of(quote)))

    assert "Northfield Supply Quotation" in text
    assert "Number NF-2026-000001" in text
    assert "Acme" in text
    assert "Account C-1001" in text
    assert "Prepared by Jordan Rep" in text
    assert "FAS-M6-100" in text
    assert "PPE-GLV-L" in text
    assert f"Total {money(quote.totals.total)}" in text
    assert "Delivery within two weeks." in text
    assert "not an offer" not in text
    for line in quote.lines:  # what the lines cost Northfield never reaches the customer
        assert amount(line.pricing.cost_total) not in text


async def test_quote_pdf_embeds_the_brand_s_fonts_and_nothing_else() -> None:
    pdf = await WeasyPrintQuoteDocuments().render(document_of(quoted()))

    # Public Sans for the text and the running footer, Source Serif 4 for "Quotation" alone.
    assert fonts_of(pdf) == {"Public-Sans", "Public-Sans-Semi-Bold", "Source-Serif-4-Semi-Bold"}


@pytest.mark.parametrize(
    ("status", "notice"),
    [
        (QuoteStatus.DRAFT, 'warning">Draft, not an offer'),
        (QuoteStatus.PENDING_APPROVAL, 'warning">Draft, not an offer'),
        (QuoteStatus.REJECTED, 'warning">Draft, not an offer'),
        (QuoteStatus.EXPIRED, 'danger">This offer has expired'),
        (QuoteStatus.CANCELLED, 'danger">Cancelled'),
        (QuoteStatus.SUPERSEDED, 'info">Replaced by a later revision'),
    ],
)
def test_quote_document_says_when_it_is_not_an_offer(status: QuoteStatus, notice: str) -> None:
    html = WeasyPrintQuoteDocuments().html(document_of(quoted(), status))

    assert f'<p class="notice {notice}' in html  # in its status color's text, on its surface


def test_quote_document_takes_every_style_from_the_pinned_tokens() -> None:
    """``just brand`` replaces the tokens; the template must use only properties they define."""
    tokens_css = (BRAND / "tokens.css").read_text(encoding="utf-8")
    defined = set(re.findall(r"^\s*(--[a-z0-9-]+):", tokens_css, flags=re.MULTILINE))
    template = (TEMPLATES / "quote.html").read_text(encoding="utf-8")
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", template))

    assert used
    assert used <= defined
    assert re.search(r"#[0-9a-fA-F]{3,6}\b", template) is None  # no color of its own
    assert f"pricewright-design {DESIGN_RELEASE}:" in tokens_css
    built = json.loads((BRAND / "tokens.json").read_text(encoding="utf-8"))
    assert (built["name"], built["version"]) == ("pricewright-design", DESIGN_RELEASE)


def test_quote_document_escapes_what_people_typed() -> None:
    quote = quoted()
    quote.notes = "<script>alert('x')</script>"

    html = WeasyPrintQuoteDocuments().html(document_of(quote))

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_document_figures_read_as_people_write_them() -> None:
    assert amount(usd("1234.5")) == "1,234.50"
    assert unit_price(usd("12.5")) == "12.50"
    assert unit_price(usd("11.2375")) == "11.2375"
    assert unit_price(Money(Decimal(1500), "JPY")) == "1,500"
    assert net_price(usd("1234.5")) == "1,234.5000"
    assert money(usd("1833.29")) == "USD 1,833.29"
    assert percent(Decimal("0.145")) == "14.5%"
    assert percent(Decimal("0.0725")) == "7.25%"
    assert quantity(Decimal("12.500"), BOLTS.unit) == "12.5 each"
    assert long_date(date(2026, 10, 5)) == "October 5, 2026"
    assert discount(usd("100"), usd("85.5")) == Decimal("0.145")
    assert discount(usd("0"), usd("0")) == 0
    assert sentence_end("Acme") == "Acme."
    assert sentence_end("Larkspur Tool Co.") == "Larkspur Tool Co."
    assert first_name("Jordan Patel") == "Jordan"


async def render(
    database: InMemoryDatabase, caller: Principal, quote: Quote, clock: FakeClock
) -> tuple[bytes, QuoteDocument]:
    documents = FakeQuoteDocuments()

    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(database)

    rendered = await render_quote(
        caller, quote.id, unit_of_work=unit_of_work, documents=documents, clock=clock
    )
    assert rendered.filename == f"{quote.display_number}.pdf"
    [document] = documents.rendered
    return rendered.content, document


async def test_render_quote_reads_the_quote_as_it_stands_now() -> None:
    rep = User.create(
        tenant_id=NORTHFIELD.id,
        email="jordan@northfield.example",
        full_name="Jordan Rep",
        role=Role.SALES_REP,
        password_hash="hashed:secret",
    )
    quote = quoted()
    quote.created_by = Actor.person(rep.id)
    quote.submit(by=REP, context=context())
    quote.send(now=NOW)
    database = InMemoryDatabase(
        tenants={NORTHFIELD.id: NORTHFIELD},
        customers={ACME.id: ACME},
        users={rep.id: rep},
        quotes={quote.id: quote},
    )
    caller = Principal(NORTHFIELD.id, uuid.uuid7(), Role.SALES_MANAGER)
    later = FakeClock(NOW + timedelta(days=45))  # past its date: it reads as expired

    content, document = await render(database, caller, quote, later)

    assert content == b"%PDF-fake NF-2026-000001 expired"
    assert (document.status, document.prepared_by) == (QuoteStatus.EXPIRED, "Jordan Rep")
    assert (document.tenant, document.customer) == (NORTHFIELD, ACME)


async def test_render_quote_names_no_one_for_an_integration_s_quote() -> None:
    quote = quoted()
    quote.created_by = Actor(ActorType.SERVICE_ACCOUNT, uuid.uuid7())
    database = InMemoryDatabase(
        tenants={NORTHFIELD.id: NORTHFIELD}, customers={ACME.id: ACME}, quotes={quote.id: quote}
    )
    caller = Principal(NORTHFIELD.id, uuid.uuid7(), scopes=frozenset({Permission.QUOTES_READ}))

    _, document = await render(database, caller, quote, FakeClock(NOW))

    assert document.prepared_by is None


async def test_render_quote_needs_quotes_read_and_the_caller_s_own_quote() -> None:
    quote = quoted()
    database = InMemoryDatabase(
        tenants={NORTHFIELD.id: NORTHFIELD}, customers={ACME.id: ACME}, quotes={quote.id: quote}
    )
    nobody = Principal(NORTHFIELD.id, uuid.uuid7(), scopes=frozenset())
    other_tenant = Principal(uuid.uuid7(), uuid.uuid7(), Role.ADMIN)

    with pytest.raises(PermissionDeniedError):
        await render(database, nobody, quote, FakeClock(NOW))
    with pytest.raises(NotFoundError):
        await render(database, other_tenant, quote, FakeClock(NOW))
