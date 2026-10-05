"""Invariants of converting quotes into orders (handbook §7, ADR-0023).

- Whatever lines, prices, overrides, currency and tax rate a quote ends with, its order carries
  exactly the same lines and totals, and they reconcile.
- A quote converts only while accepted, at any date, and at most once; a refused conversion
  changes nothing.
"""

import contextlib
import copy
from datetime import timedelta
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from pricewright.domain.errors import DomainError
from pricewright.domain.money import Money
from pricewright.domain.orders import Order
from pricewright.domain.quote_lifecycle import InvalidTransitionError, QuoteStatus
from pricewright.domain.quotes import PricingContext, Quote
from tests.unit.quote_data import ACME, MANAGER, NOW, REP, context
from tests.unit.test_quote_properties import Edit, apply, assert_totals_reconcile, scenarios


def accepted(pricing: PricingContext, steps: list[Edit]) -> Quote | None:
    """The quote the edits leave, submitted, approved by a manager if needed, sent and accepted."""
    quote = Quote.draft(
        number="NF-2026-000001",
        valid_until=NOW.date() + timedelta(days=30),
        by=REP,
        context=pricing,
    )
    for edit in steps:
        with contextlib.suppress(DomainError):  # a refused edit changes nothing
            apply(quote, edit, pricing)
    if not quote.lines:
        return None
    quote.submit(by=REP, context=pricing)
    if quote.status is QuoteStatus.PENDING_APPROVAL:
        quote.approve(by=MANAGER, now=NOW)
    quote.send(now=NOW)
    quote.accept(now=NOW)
    return quote


def assert_order_reconciles(order: Order) -> None:
    zero = Money(Decimal(0), order.currency)
    totals = order.totals
    assert totals.net_subtotal == sum((line.pricing.net_total for line in order.lines), zero)
    assert totals.list_subtotal == sum((line.pricing.list_total for line in order.lines), zero)
    assert totals.total == totals.net_subtotal + totals.tax


@given(scenario=scenarios(), days_later=st.integers(0, 400))
def test_an_order_carries_exactly_the_accepted_quotes_lines_and_totals(
    scenario: tuple[PricingContext, list[Edit]], days_later: int
) -> None:
    pricing, steps = scenario
    quote = accepted(pricing, steps)
    if quote is None:
        return
    snapshot = copy.deepcopy(quote)

    order = quote.convert(
        number="ORD-2026-000001",
        customer=pricing.customer,
        by=REP,
        now=NOW + timedelta(days=days_later),
    )

    assert [
        (line.product_id, line.sku, line.quantity, line.pricing, line.override)
        for line in order.lines
    ] == [
        (line.product_id, line.sku, line.quantity, line.pricing, line.override)
        for line in snapshot.lines
    ]
    quoted = snapshot.totals
    assert (
        order.totals.list_subtotal,
        order.totals.net_subtotal,
        order.totals.tax_rate,
        order.totals.tax,
        order.totals.total,
    ) == (quoted.list_subtotal, quoted.net_subtotal, quoted.tax_rate, quoted.tax, quoted.total)
    assert order.currency == snapshot.currency == pricing.settings.currency
    assert_order_reconciles(order)
    assert_totals_reconcile(quote)  # the quote's own snapshot is untouched
    assert (quote.lines, quote.totals) == (snapshot.lines, snapshot.totals)


@given(
    status=st.sampled_from(QuoteStatus),
    days_from_validity=st.integers(-60, 60),
    attempts=st.integers(1, 4),
)
def test_a_quote_converts_only_while_accepted_and_at_most_once(
    status: QuoteStatus, days_from_validity: int, attempts: int
) -> None:
    quote = Quote.draft(number="NF-2026-000001", valid_until=NOW.date(), by=REP, context=context())
    quote.status = status  # any status the table knows, as if the quote had reached it
    now = NOW + timedelta(days=days_from_validity)
    orders: list[Order] = []

    for _ in range(attempts):
        before = copy.deepcopy(quote)
        try:
            orders.append(quote.convert(number="ORD-2026-000001", customer=ACME, by=REP, now=now))
        except InvalidTransitionError:
            assert quote == before

    assert len(orders) == (1 if status is QuoteStatus.ACCEPTED else 0)
    assert quote.status is (QuoteStatus.CONVERTED if orders else status)
    assert quote.order_id == (orders[0].id if orders else None)
