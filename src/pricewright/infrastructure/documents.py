"""Quote PDFs with Jinja2 and WeasyPrint (ADR-0027): an HTML template, printed to PDF.

HTML and CSS keep documents and emails one family with the product's design tokens (handbook
§14). Rendering is CPU-bound, so it runs in a thread and never blocks the event loop.
"""

import asyncio
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from pricewright.application.documents import QuoteDocument
from pricewright.domain.catalog import UnitOfMeasure
from pricewright.domain.money import Money

TEMPLATES = Path(__file__).parent / "templates"
"""Quote documents and emails, one family (handbook §14)."""
UNIT_PRICE_PLACES = 4  # unit prices keep four places through the waterfall (ADR-0004)


def amount(money: Money) -> str:
    """``1,234.50``: grouped, to the currency's minor units."""
    return f"{money.amount:,.{money.minor_units}f}"


def unit_price(money: Money) -> str:
    """To the currency's minor units, or up to four places when the price has more."""
    places = money.minor_units
    while places < UNIT_PRICE_PLACES and money.amount != round(money.amount, places):
        places += 1
    return f"{money.amount:,.{places}f}"


def percent(rate: Decimal) -> str:
    """``14.5%``, without trailing zeros."""
    value = (rate * 100).normalize()
    return f"{value:f}%"


def quantity(value: Decimal, unit: UnitOfMeasure) -> str:
    return f"{value.normalize():f} {unit.name.lower()}"


def long_date(day: date | datetime) -> str:
    """``October 15, 2026``."""
    return f"{day:%B} {day.day}, {day.year}"


def discount(line_list: Money, line_net: Money) -> Decimal:
    """The share of the list price given away on a line; zero for a line at list price."""
    if line_list.amount == 0:
        return Decimal(0)
    return (1 - line_net.amount / line_list.amount).quantize(Decimal("0.001"))


def environment() -> Environment:
    """Templates with the documents' filters; HTML is escaped, plain text is not."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    env.filters.update(
        amount=amount,
        unit_price=unit_price,
        percent=percent,
        quantity=quantity,
        long_date=long_date,
    )
    env.globals.update(discount=discount)
    return env


class WeasyPrintQuoteDocuments:
    def __init__(self) -> None:
        self._template = environment().get_template("quote.html")

    def html(self, document: QuoteDocument) -> str:
        return self._template.render(
            document=document,
            tenant=document.tenant,
            quote=document.quote,
            customer=document.customer,
        )

    async def render(self, document: QuoteDocument) -> bytes:
        return await asyncio.to_thread(self._render, document)

    def _render(self, document: QuoteDocument) -> bytes:
        # Imported here: WeasyPrint loads Pango when imported, and only rendering needs it.
        from weasyprint import HTML

        pdf: bytes = HTML(string=self.html(document), base_url=str(TEMPLATES)).write_pdf()
        return pdf
