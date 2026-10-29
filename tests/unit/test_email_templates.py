"""The emails' templates (ADR-0028) as pricewright-design's docs/brand/documents.md designs them:
inline styles copied from the pinned tokens, presentation tables, a hidden preview line, and the
same blocks in the plain-text part."""

import re
from datetime import date
from decimal import Decimal

import pytest

from pricewright.domain.money import Money
from pricewright.infrastructure.documents import (
    EmailStyle,
    design_tokens,
    email_style,
    environment,
)

TEMPLATES = environment()
VALUES: dict[str, object] = {
    "tenant": "Larkspur Tool Co.",
    "number": "LT-2026-000042",
    "customer": "Harbor & Pine Co.",
    "total": Money(Decimal("1833.29"), "USD"),
    "valid_until": date(2026, 11, 13),
    "rep": "Jordan Patel",
    "rep_email": "jordan@larkspur.example",
    "decider": "Morgan Reyes",
    "comment": "Keep the bolts above their margin floor.",
}
EMAILS = ["quote_sent", "quote_approved", "quote_rejected"]


def render(name: str, kind: str, **values: object) -> str:
    return TEMPLATES.get_template(f"email/{name}.{kind}").render({**VALUES, **values})


def test_email_style_copies_the_pinned_tokens_with_sizes_in_pixels() -> None:
    tokens = design_tokens()

    assert email_style(tokens) == EmailStyle(
        accent=tokens["color-tenant-accent"],
        text=tokens["color-text-default"],
        muted=tokens["color-text-muted"],
        canvas=tokens["color-surface-muted"],
        paper=tokens["color-surface-default"],
        border=tokens["color-border-default"],
        quote=tokens["color-border-strong"],
        serif=tokens["font-family-serif"],
        sans=tokens["font-family-sans"],
        xs=12,
        md=16,
        lg=18,
        xl2=24,
    )
    assert tokens["font-family-serif"].startswith("'Source Serif 4', Georgia")


@pytest.mark.parametrize("name", EMAILS)
def test_email_html_is_inline_styled_on_presentation_tables(name: str) -> None:
    html = render(name, "html")
    style = email_style(design_tokens())

    assert '<html lang="en" dir="ltr">' in html
    assert '<meta name="color-scheme" content="light">' in html
    # Mail clients drop <style> blocks and do not resolve var(): every value is copied inline.
    assert "<style" not in html
    assert "var(" not in html
    for value in (style.accent, style.canvas, style.paper, style.border, style.text):
        assert value in html
    # Layout tables are presentation; the facts are the one data table, with row headers.
    tables = re.findall(r"<table[^>]*>", html)
    assert sum('role="presentation"' in table for table in tables) == 2
    assert len(tables) == 3
    assert '<th scope="row"' in html
    assert re.search(r'<div style="display:none;[^"]*" aria-hidden="true">.+</div>', html)


@pytest.mark.parametrize("name", EMAILS)
def test_email_escapes_what_people_typed(name: str) -> None:
    html = render(name, "html", customer="<b>Acme</b> & Sons", comment="<script>x</script>")

    assert "<b>" not in html
    assert "<script>" not in html
    assert "&amp; Sons" in html


def test_a_sent_quote_reads_formally_with_its_facts() -> None:
    html = render("quote_sent", "html")
    text = render("quote_sent", "txt")

    assert "<title>Quotation LT-2026-000042 from Larkspur Tool Co.</title>" in html
    assert "Your quotation from Larkspur Tool Co.</h1>" in html
    # A sentence that ends with "Co." gets no second period.
    assert (
        "Jordan Patel at Larkspur Tool Co. sends you a quotation for Harbor &amp; Pine Co.<" in html
    )
    assert text == (
        "Hello,\n\n"
        "Jordan Patel at Larkspur Tool Co. sends you a quotation for Harbor & Pine Co.\n\n"
        "Quotation: LT-2026-000042\nTotal: USD 1,833.29\nValid until: November 13, 2026\n\n"
        "The quotation is attached as a PDF. Reply to this email to reach Jordan Patel.\n\n"
        "Larkspur Tool Co., sent with Pricewright.\n"
    )


def test_a_sent_quote_without_a_rep_comes_from_the_tenant() -> None:
    text = render("quote_sent", "txt", rep=None)

    assert "Larkspur Tool Co. sends you a quotation" in text
    assert "Reply to" not in text


def test_a_decision_quotes_the_manager_s_comment() -> None:
    html = render("quote_rejected", "html")
    text = render("quote_rejected", "txt")

    assert "Your quote needs changes</h1>" in html
    assert "Morgan Reyes asked for changes: “Keep the bolts above their margin floor.”" in html
    assert re.search(
        r"<blockquote [^>]*>Keep the bolts above their margin floor.</blockquote>", html
    )
    assert text.startswith(
        "Hi Jordan,\n\n"
        "Morgan Reyes asked for changes to your quote for Harbor & Pine Co.:\n\n"
        "> Keep the bolts above their margin floor.\n\n"
        "Quote: LT-2026-000042\nCustomer: Harbor & Pine Co.\nTotal: USD 1,833.29\n\n"
        "Revise the quote and submit it again, or cancel it, in Pricewright.\n\n"
    )


def test_an_approval_without_a_comment_skips_the_quotation() -> None:
    html = render("quote_approved", "html", comment=None)
    text = render("quote_approved", "txt", comment=None)

    assert "<blockquote" not in html
    assert "Morgan Reyes approved it. It's ready to send.</div>" in html
    assert (
        "Morgan Reyes approved your quote for Harbor & Pine Co.\n\nQuote: LT-2026-000042\n" in text
    )
    assert text.endswith(
        "the offer is valid until November 13, 2026.\n\n"
        "Larkspur Tool Co., sent with Pricewright. You get this email because you submitted the"
        " quote.\n"
    )
