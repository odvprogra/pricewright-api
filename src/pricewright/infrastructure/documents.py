"""Quote PDFs with Jinja2 and WeasyPrint (ADR-0027): an HTML template, printed to PDF.

HTML and CSS keep documents and emails one family with the product's design tokens (handbook
§14): both follow pricewright-design's ``docs/brand/documents.md``, on the tokens of the release
pinned in ``templates/brand/`` (D-61). Rendering is CPU-bound, so it runs in a thread and never
blocks the event loop.
"""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from pricewright.application.documents import QuoteDocument
from pricewright.domain.catalog import UnitOfMeasure
from pricewright.domain.money import Money

TEMPLATES = Path(__file__).parent / "templates"
"""Quote documents and emails, one family (handbook §14)."""
BRAND = TEMPLATES / "brand"
"""The pinned pricewright-design release's ``tokens.css`` and ``tokens.json``, and the fonts."""
DESIGN_RELEASE = "0.1.0"
"""The pricewright-design release in ``brand/``: ``just brand <version>`` replaces it."""
UNIT_PRICE_PLACES = 4  # unit prices keep four places through the waterfall (ADR-0004)
REM_PX = 16  # emails size the rem tokens in pixels: mail clients handle rem unevenly


def amount(money: Money) -> str:
    """``1,234.50``: grouped, to the currency's minor units."""
    return f"{money.amount:,.{money.minor_units}f}"


def money(value: Money) -> str:
    """``USD 1,833.29``: a total as customer documents write it."""
    return f"{value.currency} {amount(value)}"


def unit_price(money: Money) -> str:
    """To the currency's minor units, or up to four places when the price has more."""
    places = money.minor_units
    while places < UNIT_PRICE_PLACES and money.amount != round(money.amount, places):
        places += 1
    return f"{money.amount:,.{places}f}"


def net_price(money: Money) -> str:
    """Always four places, so the net prices' column lines up as in the app's builder."""
    return f"{money.amount:,.{UNIT_PRICE_PLACES}f}"


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


def sentence_end(name: str) -> str:
    """A sentence that ends with a name: no second period after ``Co.``."""
    return name if name.endswith(".") else f"{name}."


def first_name(full_name: str) -> str:
    return full_name.split(" ", 1)[0]


@dataclass(frozen=True, slots=True)
class EmailStyle:
    """The values an email uses, copied from ``tokens.json``: mail clients drop ``<style>``
    blocks and do not resolve ``var()``, so every style is inline."""

    accent: str
    text: str
    muted: str
    canvas: str
    paper: str
    border: str
    quote: str
    serif: str
    sans: str
    xs: int
    md: int
    lg: int
    xl2: int


def design_tokens() -> Mapping[str, str]:
    """The pinned release's built tokens, by name (``color-text-default``)."""
    built = json.loads((BRAND / "tokens.json").read_text(encoding="utf-8"))
    tokens: dict[str, str] = built["tokens"]
    return tokens


def email_style(tokens: Mapping[str, str]) -> EmailStyle:
    def px(name: str) -> int:
        return round(float(tokens[name].removesuffix("rem")) * REM_PX)

    return EmailStyle(
        accent=tokens["color-tenant-accent"],  # the default until tenants have their own (M7)
        text=tokens["color-text-default"],
        muted=tokens["color-text-muted"],
        canvas=tokens["color-surface-muted"],
        paper=tokens["color-surface-default"],
        border=tokens["color-border-default"],
        quote=tokens["color-border-strong"],
        serif=tokens["font-family-serif"],
        sans=tokens["font-family-sans"],
        xs=px("font-size-xs"),
        md=px("font-size-md"),
        lg=px("font-size-lg"),
        xl2=px("font-size-2xl"),
    )


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
        money=money,
        unit_price=unit_price,
        net_price=net_price,
        percent=percent,
        quantity=quantity,
        long_date=long_date,
        sentence_end=sentence_end,
        first_name=first_name,
    )
    env.globals.update(discount=discount, style=email_style(design_tokens()))
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
        from weasyprint.text.fonts import FontConfiguration

        # The template's @font-face rules take effect only through a font configuration; one per
        # render, so renders running in their own threads never share one.
        fonts = FontConfiguration()
        pdf: bytes = HTML(string=self.html(document), base_url=str(TEMPLATES)).write_pdf(
            font_config=fonts
        )
        return pdf
