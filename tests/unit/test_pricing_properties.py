"""Invariants of the pricing engine over generated catalogs, rules and lines (handbook §7).

The oracles here recompute what the engine promises from the inputs, never from its internals.
"""

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

from hypothesis import given
from hypothesis import strategies as st

from pricewright.domain.catalog import Product, UnitOfMeasure
from pricewright.domain.currencies import MINOR_UNITS
from pricewright.domain.customers import Customer, CustomerTier
from pricewright.domain.money import Money, round_half_up
from pricewright.domain.pricing import (
    ApprovalReason,
    LineRequest,
    ManualOverride,
    PricedQuote,
    PriceOverride,
    RateOverride,
    Stage,
    price_quote,
)
from pricewright.domain.pricing_rules import Bracket, PricingRule, RuleKind
from pricewright.domain.tenants import TenantSettings

TENANT = uuid.uuid7()
CATEGORIES = (uuid.uuid7(), uuid.uuid7())
NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
STAGE_KINDS = (RuleKind.VOLUME_TIER, RuleKind.CUSTOMER_TIER, RuleKind.PROMOTION)

amounts = st.decimals(min_value=0, max_value=10_000, places=4)
quantities = st.decimals(min_value=Decimal("0.001"), max_value=1_000, places=3)
discounts = st.decimals(min_value=Decimal("0.0001"), max_value=1, places=4)
floors = st.decimals(min_value=0, max_value=Decimal("0.9999"), places=4)


@dataclass(frozen=True)
class Scenario:
    customer: Customer
    lines: tuple[LineRequest, ...]
    settings: TenantSettings
    rules: tuple[PricingRule, ...]

    def price(self, rules: tuple[PricingRule, ...] | None = None) -> PricedQuote:
        return price_quote(
            self.customer,
            self.lines,
            settings=self.settings,
            rules=self.rules if rules is None else rules,
            at=NOW,
        )

    def effective_rules(self, line: LineRequest) -> list[PricingRule]:
        return [
            rule
            for rule in self.rules
            if rule.is_active
            and rule.valid_from <= NOW
            and (rule.valid_to is None or rule.valid_to > NOW)
            and rule.covers(line.product, self.customer.tier)
        ]


@st.composite
def products(draw: st.DrawFn, currency: str) -> Product:
    return Product.create(
        tenant_id=TENANT,
        currency=currency,
        sku="SKU-1",
        name="Generated product",
        unit=UnitOfMeasure.EACH,
        list_price=Money(draw(amounts), currency),
        unit_cost=Money(draw(amounts), currency),
        category_id=draw(st.sampled_from((*CATEGORIES, None))),
    )


@st.composite
def pricing_rules(draw: st.DrawFn, catalog: list[Product]) -> PricingRule:
    kind = draw(st.sampled_from(RuleKind))
    scope = draw(st.sampled_from(["all", "category", "product"]))
    start = NOW + timedelta(hours=draw(st.integers(-48, 6)))
    end = draw(st.none() | st.integers(1, 96).map(lambda hours: start + timedelta(hours=hours)))
    brackets = draw(
        st.lists(st.tuples(quantities, discounts), min_size=1, max_size=4, unique_by=lambda b: b[0])
    )
    rule = PricingRule.create(
        tenant_id=TENANT,
        kind=kind,
        name=f"{kind} rule",
        valid_from=start,
        valid_to=start + timedelta(hours=72) if kind is RuleKind.PROMOTION and end is None else end,
        rate=None
        if kind is RuleKind.VOLUME_TIER
        else draw(floors if kind is RuleKind.MARGIN_FLOOR else discounts),
        brackets=[Bracket(*b) for b in brackets] if kind is RuleKind.VOLUME_TIER else (),
        product_id=draw(st.sampled_from([p.id for p in catalog])) if scope == "product" else None,
        category_id=draw(st.sampled_from(CATEGORIES)) if scope == "category" else None,
        customer_tier=draw(st.sampled_from(CustomerTier))
        if kind is RuleKind.CUSTOMER_TIER
        else None,
    )
    rule.change(is_active=draw(st.booleans()))
    return rule


def overrides(currency: str) -> st.SearchStrategy[ManualOverride]:
    return st.builds(RateOverride, discounts, st.just("Negotiated")) | st.builds(
        PriceOverride, amounts.map(lambda amount: Money(amount, currency)), st.just("Agreed price")
    )


@st.composite
def scenarios(draw: st.DrawFn, *, with_overrides: bool = True) -> Scenario:
    currency = draw(st.sampled_from(["USD", "JPY", "KWD"]))
    catalog = draw(st.lists(products(currency), min_size=1, max_size=4))
    lines = tuple(
        LineRequest(
            product,
            draw(quantities),
            draw(st.none() | overrides(currency)) if with_overrides else None,
        )
        for product in catalog
    )
    settings = TenantSettings(
        currency=currency,
        tax_rate=draw(st.decimals(min_value=0, max_value=Decimal("0.9999"), places=4)),
        approval_threshold=draw(st.decimals(min_value=0, max_value=1, places=4)),
    )
    customer = Customer.create(
        tenant_id=TENANT,
        account_number="C-1",
        name="Generated customer",
        tier=draw(st.sampled_from(CustomerTier)),
    )
    rules = tuple(draw(st.lists(pricing_rules(catalog), max_size=8)))
    return Scenario(customer, lines, settings, rules)


@given(scenarios())
def test_every_breakdown_adds_up_to_its_net_unit_price(scenario: Scenario) -> None:
    for line in scenario.price().lines:
        price = line.breakdown.list_unit_price
        for step in line.breakdown.steps:
            price += step.amount
            assert step.unit_price == price

        assert price == line.breakdown.net_unit_price


@given(scenarios())
def test_totals_always_reconcile(scenario: Scenario) -> None:
    priced = scenario.price()
    places = MINOR_UNITS[scenario.settings.currency]

    for line in priced.lines:
        unit_price = line.breakdown.net_unit_price.amount
        assert line.net_total.amount == round_half_up(unit_price * line.quantity, places)
        list_price = line.breakdown.list_unit_price.amount
        assert line.list_total.amount == round_half_up(list_price * line.quantity, places)
    assert priced.net_subtotal.amount == sum(line.net_total.amount for line in priced.lines)
    assert priced.list_subtotal.amount == sum(line.list_total.amount for line in priced.lines)
    assert priced.tax.amount == round_half_up(
        priced.net_subtotal.amount * scenario.settings.tax_rate, places
    )
    assert priced.total == priced.net_subtotal + priced.tax


@given(scenarios(with_overrides=False))
def test_rules_only_ever_lower_a_price_and_never_below_zero(scenario: Scenario) -> None:
    priced = scenario.price()

    for line in priced.lines:
        assert all(step.amount.amount <= 0 for step in line.breakdown.steps)
        assert 0 <= line.breakdown.net_unit_price.amount <= line.breakdown.list_unit_price.amount
        assert line.net_total.amount <= line.list_total.amount
    assert 0 <= priced.discount <= 1


@given(scenarios())
def test_each_stage_applies_the_best_effective_rule_or_none(scenario: Scenario) -> None:
    priced = scenario.price()

    for request, line in zip(scenario.lines, priced.lines, strict=True):
        applied = {step.stage: step.rate for step in line.breakdown.steps}
        for kind in STAGE_KINDS:
            rates = [
                rate
                for rule in scenario.effective_rules(request)
                if rule.kind is kind and (rate := rule.rate_for(request.quantity)) is not None
            ]
            assert applied.get(Stage(kind)) == (max(rates) if rates else None)


@given(scenarios())
def test_no_line_falls_below_its_margin_floor_without_needing_approval(scenario: Scenario) -> None:
    priced = scenario.price()

    for request, line in zip(scenario.lines, priced.lines, strict=True):
        floors = [r for r in scenario.effective_rules(request) if r.kind is RuleKind.MARGIN_FLOOR]
        most_specific = max((r.specificity for r in floors), default=None)
        floor = max(
            (r.rate for r in floors if r.specificity == most_specific and r.rate is not None),
            default=None,
        )
        places = MINOR_UNITS[scenario.settings.currency]
        cost = round_half_up(request.product.unit_cost.amount * request.quantity, places)
        net = line.net_total.amount
        if floor is not None and net - cost < floor * net:
            assert line.below_margin_floor
            assert ApprovalReason.LINE_BELOW_MARGIN_FLOOR in priced.approval_reasons


@given(scenarios())
def test_approval_is_needed_exactly_above_the_threshold_or_below_a_floor(
    scenario: Scenario,
) -> None:
    priced = scenario.price()
    list_subtotal, net_subtotal = priced.list_subtotal.amount, priced.net_subtotal.amount
    exact_discount = 1 - Fraction(net_subtotal) / Fraction(list_subtotal) if list_subtotal else 0
    above = exact_discount > Fraction(scenario.settings.approval_threshold)

    assert priced.requires_approval == (
        above or any(line.below_margin_floor for line in priced.lines)
    )


@given(scenarios(), st.randoms(use_true_random=False))
def test_the_order_of_the_rules_does_not_change_the_result(
    scenario: Scenario, shuffler: random.Random
) -> None:
    shuffled = list(scenario.rules)
    shuffler.shuffle(shuffled)

    assert scenario.price(tuple(shuffled)) == scenario.price()
