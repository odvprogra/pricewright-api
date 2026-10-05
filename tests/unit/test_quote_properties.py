"""Invariants of draft quotes under any sequence of edits (handbook §7).

The oracle recomputes the totals from the stored line snapshots: whatever the edits, a quote's
totals reconcile with its lines, and an edit that fails changes nothing.
"""

import copy
import uuid
from dataclasses import dataclass
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from pricewright.domain.catalog import Product, UnitOfMeasure
from pricewright.domain.customers import Customer
from pricewright.domain.errors import DomainError
from pricewright.domain.money import Money, round_half_up
from pricewright.domain.pricing import ManualOverride, PriceOverride, RateOverride
from pricewright.domain.quotes import LineChange, PricingContext, Quote
from pricewright.domain.tenants import TenantSettings
from tests.unit.quote_data import NOW, REP, TENANT

amounts = st.decimals(min_value=0, max_value=10_000, places=4)
quantities = st.decimals(min_value=Decimal("0.001"), max_value=1_000, places=3)
rates = st.decimals(min_value=Decimal("0.0001"), max_value=1, places=4)


def overrides(currency: str) -> st.SearchStrategy[ManualOverride]:
    return st.builds(RateOverride, rates, st.just("Negotiated")) | st.builds(
        PriceOverride, amounts.map(lambda amount: Money(amount, currency)), st.just("Agreed")
    )


@dataclass(frozen=True)
class Add:
    product: int
    quantity: Decimal
    override: ManualOverride | None


@dataclass(frozen=True)
class Change:
    line: int
    quantity: Decimal


@dataclass(frozen=True)
class Remove:
    line: int


type Edit = Add | Change | Remove


def edits(currency: str) -> st.SearchStrategy[Edit]:
    return st.one_of(
        st.builds(Add, st.integers(0, 3), quantities, st.none() | overrides(currency)),
        st.builds(Change, st.integers(0, 50), quantities),
        st.builds(Remove, st.integers(0, 50)),
    )


@st.composite
def scenarios(draw: st.DrawFn) -> tuple[PricingContext, list[Edit]]:
    currency = draw(st.sampled_from(["USD", "JPY", "KWD"]))
    catalog = [
        Product.create(
            tenant_id=TENANT,
            currency=currency,
            sku=f"SKU-{index}",
            name=f"Product {index}",
            unit=UnitOfMeasure.EACH,
            list_price=Money(draw(amounts), currency),
            unit_cost=Money(draw(amounts), currency),
        )
        for index in range(4)
    ]
    settings = TenantSettings(
        currency=currency,
        tax_rate=draw(st.decimals(min_value=0, max_value=Decimal("0.9999"), places=4)),
        approval_threshold=draw(st.decimals(min_value=0, max_value=1, places=4)),
    )
    customer = Customer.create(tenant_id=TENANT, account_number="C-1", name="Generated")
    pricing = PricingContext(
        customer=customer,
        settings=settings,
        rules=(),
        products={item.id: item for item in catalog},
        at=NOW,
    )
    return pricing, draw(st.lists(edits(currency), max_size=25))


def apply(quote: Quote, edit: Edit, pricing: PricingContext) -> None:
    products = list(pricing.products)
    lines = [line.id for line in quote.lines] or [uuid.uuid7()]
    match edit:
        case Add(product, units, override):
            change = LineChange(products[product], units, override)
            quote.add_line(change, by=REP, context=pricing)
        case Change(line, units):
            quote.change_line(lines[line % len(lines)], quantity=units, by=REP, context=pricing)
        case Remove(line):
            quote.remove_line(lines[line % len(lines)], context=pricing)


def assert_totals_reconcile(quote: Quote) -> None:
    totals = quote.totals
    zero = Money(Decimal(0), quote.currency)
    minor = zero.minor_units
    for line in quote.lines:
        unit_price = line.pricing.breakdown.net_unit_price.amount
        assert line.quantity == line.pricing.quantity
        assert line.pricing.net_total.amount == round_half_up(unit_price * line.quantity, minor)
    assert totals.net_subtotal == sum((line.pricing.net_total for line in quote.lines), zero)
    assert totals.list_subtotal == sum((line.pricing.list_total for line in quote.lines), zero)
    assert totals.tax.amount == round_half_up(totals.net_subtotal.amount * totals.tax_rate, minor)
    assert totals.total == totals.net_subtotal + totals.tax


@given(scenario=scenarios())
def test_quote_totals_always_reconcile_whatever_the_edits(
    scenario: tuple[PricingContext, list[Edit]],
) -> None:
    pricing, steps = scenario
    quote = Quote.draft(number="NF-2026-000001", valid_until=NOW.date(), by=REP, context=pricing)

    for edit in steps:
        before = copy.deepcopy(quote)
        try:
            apply(quote, edit, pricing)
        except DomainError:
            assert quote == before  # a refused edit changes nothing
        assert_totals_reconcile(quote)
