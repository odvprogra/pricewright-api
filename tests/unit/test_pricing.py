"""The pricing engine with worked examples. Its invariants are in ``test_pricing_properties.py``."""

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from pricewright.domain.catalog import Product, UnitOfMeasure
from pricewright.domain.customers import Customer, CustomerTier
from pricewright.domain.money import InvalidMoneyError, Money
from pricewright.domain.pricing import (
    ApprovalReason,
    ArchivedCustomerError,
    ArchivedProductError,
    InvalidOverrideError,
    LineRequest,
    PricedQuote,
    PriceOverride,
    RateOverride,
    Stage,
    price_quote,
)
from pricewright.domain.pricing_rules import Bracket, PricingRule, RuleKind
from pricewright.domain.quantities import InvalidQuantityError
from pricewright.domain.tenants import TenantSettings

TENANT = uuid.uuid7()
FASTENERS = uuid.uuid7()
NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
USD = TenantSettings(currency="USD", tax_rate=Decimal("0.0725"))


def usd(amount: str, currency: str = "USD") -> Money:
    return Money(Decimal(amount), currency)


def product(list_price: str = "100", unit_cost: str = "60", currency: str = "USD") -> Product:
    return Product.create(
        tenant_id=TENANT,
        currency=currency,
        sku="FAS-M6-100",
        name="Hex bolt M6 x 100",
        unit=UnitOfMeasure.BOX,
        list_price=usd(list_price, currency),
        unit_cost=usd(unit_cost, currency),
        category_id=FASTENERS,
    )


def customer(tier: CustomerTier = CustomerTier.GOLD) -> Customer:
    return Customer.create(tenant_id=TENANT, account_number="C-1001", name="Acme", tier=tier)


def promotion(
    rate: str,
    name: str = "Promotion",
    *,
    product_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
) -> PricingRule:
    return PricingRule.create(
        tenant_id=TENANT,
        kind=RuleKind.PROMOTION,
        name=name,
        rate=Decimal(rate),
        valid_from=NOW - timedelta(days=1),
        valid_to=NOW + timedelta(days=1),
        product_id=product_id,
        category_id=category_id,
    )


def volume(*brackets: tuple[int, str]) -> PricingRule:
    return PricingRule.create(
        tenant_id=TENANT,
        kind=RuleKind.VOLUME_TIER,
        name="Volume",
        valid_from=NOW,
        brackets=[Bracket(Decimal(units), Decimal(rate)) for units, rate in brackets],
    )


def gold(rate: str) -> PricingRule:
    return PricingRule.create(
        tenant_id=TENANT,
        kind=RuleKind.CUSTOMER_TIER,
        name="Gold customers",
        rate=Decimal(rate),
        valid_from=NOW,
        customer_tier=CustomerTier.GOLD,
    )


def floor(rate: str, name: str = "Floor", *, category_id: uuid.UUID | None = None) -> PricingRule:
    return PricingRule.create(
        tenant_id=TENANT,
        kind=RuleKind.MARGIN_FLOOR,
        name=name,
        rate=Decimal(rate),
        valid_from=NOW,
        category_id=category_id,
    )


def quote(
    *lines: LineRequest,
    rules: Sequence[PricingRule] = (),
    settings: TenantSettings = USD,
    buyer: Customer | None = None,
) -> PricedQuote:
    return price_quote(buyer or customer(), lines, settings=settings, rules=rules, at=NOW)


def test_stages_cascade_10_percent_volume_and_5_percent_tier_make_14_5_percent() -> None:
    priced = quote(LineRequest(product(), Decimal(10)), rules=[volume((10, "0.10")), gold("0.05")])

    line = priced.lines[0]
    assert [(s.stage, s.rate, s.amount, s.unit_price) for s in line.breakdown.steps] == [
        (Stage.VOLUME_TIER, Decimal("0.10"), usd("-10"), usd("90")),
        (Stage.CUSTOMER_TIER, Decimal("0.05"), usd("-4.5"), usd("85.5")),
    ]
    assert (line.list_total, line.net_total) == (usd("1000"), usd("855"))
    assert priced.discount == Decimal("0.145")
    assert not priced.requires_approval  # 14.5% does not exceed the 15% threshold


def test_a_stage_takes_the_best_discount_and_names_its_rule() -> None:
    rules = [promotion("0.10", "Fasteners week", category_id=FASTENERS), promotion("0.12", "Fall")]

    steps = quote(LineRequest(product(), Decimal(1)), rules=rules).lines[0].breakdown.steps

    assert [(s.label, s.rate) for s in steps] == [("Fall", Decimal("0.12"))]


def test_a_tie_goes_to_the_most_specific_rule_then_the_oldest() -> None:
    bolts = product()
    oldest, newest = promotion("0.1", "Oldest"), promotion("0.1", "Newest")
    by_category = promotion("0.1", "Fasteners", category_id=FASTENERS)
    by_product = promotion("0.1", "Bolts", product_id=bolts.id)

    def winner(rules: Sequence[PricingRule]) -> str:
        return quote(LineRequest(bolts, Decimal(1)), rules=rules).lines[0].breakdown.steps[0].label

    assert winner([newest, oldest]) == "Oldest"
    assert winner([newest, by_category, oldest]) == "Fasteners"
    assert winner([by_category, by_product, oldest]) == "Bolts"


def test_rules_that_do_not_apply_leave_the_list_price() -> None:
    ended = promotion("0.2")
    ended.change(valid_to=NOW)
    inactive = promotion("0.2")
    inactive.change(is_active=False)
    rules = [
        volume((50, "0.1")),
        gold("0.05"),
        ended,
        inactive,
        promotion("0.3", product_id=uuid.uuid7()),
    ]

    line = quote(
        LineRequest(product(), Decimal(49)), rules=rules, buyer=customer(CustomerTier.SILVER)
    ).lines[0]

    assert line.breakdown.steps == ()
    assert line.breakdown.net_unit_price == usd("100")


def test_unit_prices_round_to_4_places_and_line_totals_to_cents_half_up() -> None:
    priced = quote(
        LineRequest(product("1.2345"), Decimal(1)),
        LineRequest(product("0.125"), Decimal(1)),
        rules=[promotion("0.1", product_id=uuid.uuid7())],
    )
    discounted = quote(LineRequest(product("1.2345"), Decimal(1)), rules=[promotion("0.1")])

    # 1.2345 x 0.9 = 1.11105: a tie, rounded up (half even would give 1.1110).
    assert discounted.lines[0].breakdown.net_unit_price == usd("1.1111")
    # 0.125 is a tie at the cent: 0.13, not 0.12.
    assert priced.lines[1].net_total == usd("0.13")


@pytest.mark.parametrize(
    ("currency", "list_price", "units", "line_total", "tax"),
    [
        ("JPY", "1235", "0.5", "618", "45"),  # no minor units: 617.5 → 618; tax 44.805 → 45
        ("KWD", "1.2345", "1", "1.235", "0.090"),  # three: 1.2345 → 1.235; tax 0.0895... → 0.090
    ],
)
def test_document_amounts_round_to_the_currency_minor_units(
    currency: str, list_price: str, units: str, line_total: str, tax: str
) -> None:
    settings = TenantSettings(currency=currency, tax_rate=Decimal("0.0725"))

    priced = quote(
        LineRequest(product(list_price, "0", currency), Decimal(units)), settings=settings
    )

    assert priced.lines[0].net_total == usd(line_total, currency)
    assert priced.tax == usd(tax, currency)


def test_tax_is_computed_once_on_the_net_subtotal() -> None:
    priced = quote(
        LineRequest(product("0.05"), Decimal(1)), LineRequest(product("0.05"), Decimal(1))
    )

    # Per line, 0.05 x 7.25% rounds to 0.00; on the 0.10 subtotal it is 0.00725 → 0.01.
    assert (priced.net_subtotal, priced.tax, priced.total) == (usd("0.1"), usd("0.01"), usd("0.11"))


def test_a_rate_override_comes_after_every_rule() -> None:
    line = LineRequest(product(), Decimal(1), RateOverride(Decimal("0.05"), "Matches a competitor"))

    steps = quote(line, rules=[promotion("0.1")]).lines[0].breakdown.steps

    assert [(s.stage, s.label, s.rule_id, s.unit_price) for s in steps][1:] == [
        (Stage.MANUAL_OVERRIDE, "Matches a competitor", None, usd("85.5"))
    ]


def test_a_price_override_sets_the_net_unit_price_up_or_down() -> None:
    down = LineRequest(product(), Decimal(2), PriceOverride(usd("80"), "Agreed by phone"))
    up = LineRequest(product(), Decimal(1), PriceOverride(usd("105"), "Rush order"))

    priced = quote(down, up, rules=[promotion("0.1")])

    assert [line.breakdown.steps[-1].amount for line in priced.lines] == [usd("-10"), usd("15")]
    assert [line.net_total for line in priced.lines] == [usd("160"), usd("105")]
    assert priced.lines[0].breakdown.steps[-1].rate is None


@pytest.mark.parametrize(
    "make",
    [
        lambda: RateOverride(Decimal(0), "No reason to be zero"),
        lambda: RateOverride(Decimal("0.1"), "   "),
        lambda: PriceOverride(usd("-1"), "Negative"),
        lambda: PriceOverride(usd("1"), "x" * 201),
    ],
    ids=["zero-rate", "blank-reason", "negative-price", "long-reason"],
)
def test_invalid_overrides_are_refused(make: Callable[[], object]) -> None:
    with pytest.raises(InvalidOverrideError):
        make()


def test_a_price_override_in_another_currency_is_refused() -> None:
    line = LineRequest(product(), Decimal(1), PriceOverride(usd("80", "EUR"), "Wrong currency"))

    with pytest.raises(InvalidOverrideError, match="USD"):
        quote(line)


def test_the_most_specific_margin_floor_applies() -> None:
    bolts = product("100", "85")  # 15% margin at list price
    tenant_wide, fasteners = (
        floor("0.25", "Tenant"),
        floor("0.10", "Fasteners", category_id=FASTENERS),
    )

    with_category = quote(LineRequest(bolts, Decimal(1)), rules=[tenant_wide, fasteners]).lines[0]
    without = quote(LineRequest(bolts, Decimal(1)), rules=[tenant_wide, floor("0.2")]).lines[0]

    assert with_category.margin_floor is not None
    assert (with_category.margin_floor.label, with_category.below_margin_floor) == (
        "Fasteners",
        False,
    )
    assert without.margin_floor is not None
    assert (without.margin_floor.label, without.below_margin_floor) == ("Tenant", True)
    assert (without.margin, without.margin_rate) == (usd("15"), Decimal("0.15"))


def test_approval_is_needed_above_the_threshold_or_below_a_margin_floor() -> None:
    bolts = product("100", "60")

    at_threshold = quote(LineRequest(bolts, Decimal(1)), rules=[promotion("0.15")])
    above = quote(LineRequest(bolts, Decimal(1)), rules=[promotion("0.1501")])
    thin = quote(LineRequest(bolts, Decimal(1)), rules=[promotion("0.1"), floor("0.4")])

    assert (at_threshold.discount, at_threshold.requires_approval) == (Decimal("0.15"), False)
    assert above.approval_reasons == (ApprovalReason.DISCOUNT_ABOVE_THRESHOLD,)
    assert thin.approval_reasons == (ApprovalReason.LINE_BELOW_MARGIN_FLOOR,)


def test_a_line_given_away_has_no_margin_rate_and_breaks_any_floor() -> None:
    free = LineRequest(product(), Decimal(1), PriceOverride(usd("0"), "Sample"))

    line = quote(free, rules=[floor("0")]).lines[0]

    assert (line.margin, line.margin_rate, line.below_margin_floor) == (usd("-60"), None, True)


def test_an_empty_quote_totals_zero() -> None:
    priced = quote()

    assert (priced.list_subtotal, priced.total, priced.discount) == (usd("0"), usd("0"), Decimal(0))
    assert not priced.requires_approval


def test_archived_products_and_customers_cannot_be_priced() -> None:
    archived_product, archived_customer = product(), customer()
    archived_product.change(is_active=False)
    archived_customer.change(is_active=False)

    with pytest.raises(ArchivedProductError, match="FAS-M6-100"):
        quote(LineRequest(archived_product, Decimal(1)))
    with pytest.raises(ArchivedCustomerError, match="C-1001"):
        quote(buyer=archived_customer)


def test_lines_need_a_valid_quantity_and_the_tenant_currency() -> None:
    with pytest.raises(InvalidQuantityError):
        quote(LineRequest(product(), Decimal(0)))
    with pytest.raises(InvalidMoneyError, match="do not mix"):
        quote(LineRequest(product(currency="EUR"), Decimal(1)))
