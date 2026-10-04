import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TypedDict, Unpack

import pytest

from pricewright.domain.catalog import Product, UnitOfMeasure
from pricewright.domain.customers import CustomerTier
from pricewright.domain.money import Money
from pricewright.domain.pricing_rules import (
    MAX_BRACKETS,
    Bracket,
    InvalidPricingRuleError,
    PricingRule,
    RuleKind,
)
from pricewright.domain.quantities import InvalidQuantityError

TENANT = uuid.uuid7()
FASTENERS = uuid.uuid7()
OCT_1 = datetime(2026, 10, 1, tzinfo=UTC)
NOV_1 = datetime(2026, 11, 1, tzinfo=UTC)


def bolts(category_id: uuid.UUID | None = FASTENERS) -> Product:
    return Product.create(
        tenant_id=TENANT,
        currency="USD",
        sku="FAS-M6-100",
        name="Hex bolt M6 x 100",
        unit=UnitOfMeasure.BOX,
        list_price=Money(Decimal("12.50"), "USD"),
        unit_cost=Money(Decimal("7.25"), "USD"),
        category_id=category_id,
    )


class RuleFields(TypedDict, total=False):
    name: str
    valid_to: datetime | None
    rate: Decimal | None
    brackets: Sequence[Bracket]
    product_id: uuid.UUID | None
    category_id: uuid.UUID | None
    customer_tier: CustomerTier | None


DEFAULTS: dict[RuleKind, RuleFields] = {
    RuleKind.VOLUME_TIER: {"brackets": [Bracket(Decimal(10), Decimal("0.05"))]},
    RuleKind.CUSTOMER_TIER: {"rate": Decimal("0.05"), "customer_tier": CustomerTier.GOLD},
    RuleKind.PROMOTION: {"rate": Decimal("0.10"), "valid_to": NOV_1},
    RuleKind.MARGIN_FLOOR: {"rate": Decimal("0.20")},
}


def rule(kind: RuleKind = RuleKind.PROMOTION, **fields: Unpack[RuleFields]) -> PricingRule:
    named: RuleFields = {"name": "  October promotion  "}
    return PricingRule.create(
        tenant_id=TENANT, kind=kind, valid_from=OCT_1, **(named | DEFAULTS[kind] | fields)
    )


def test_pricing_rule_create_trims_the_name_and_assigns_a_uuid7() -> None:
    created = rule()

    assert (created.name, created.rate, created.brackets) == (
        "October promotion",
        Decimal("0.10"),
        (),
    )
    assert (created.id.version, created.version, created.is_active) == (7, 1, True)


def test_volume_tier_brackets_are_sorted_and_the_highest_reached_applies_to_the_whole_line() -> (
    None
):
    tier = rule(
        RuleKind.VOLUME_TIER,
        brackets=[Bracket(Decimal(100), Decimal("0.10")), Bracket(Decimal(10), Decimal("0.05"))],
    )

    assert [b.min_quantity for b in tier.brackets] == [10, 100]
    assert tier.rate_for(Decimal("9.999")) is None
    assert tier.rate_for(Decimal(10)) == Decimal("0.05")
    assert tier.rate_for(Decimal("99.5")) == Decimal("0.05")
    assert tier.rate_for(Decimal(250)) == Decimal("0.10")


def test_other_kinds_have_one_rate_whatever_the_quantity() -> None:
    assert rule().rate_for(Decimal("0.001")) == Decimal("0.10")


@pytest.mark.parametrize(
    ("kind", "fields", "message"),
    [
        (
            RuleKind.PROMOTION,
            {"product_id": uuid.uuid7(), "category_id": FASTENERS},
            "one product, one category",
        ),
        (RuleKind.PROMOTION, {"customer_tier": CustomerTier.GOLD}, "only they, name a tier"),
        (RuleKind.CUSTOMER_TIER, {"customer_tier": None}, "only they, name a tier"),
        (RuleKind.PROMOTION, {"valid_to": None}, "time-boxed"),
        (RuleKind.PROMOTION, {"valid_to": OCT_1}, "after valid_from"),
        (RuleKind.MARGIN_FLOOR, {"valid_to": NOV_1.replace(tzinfo=None)}, "time zone"),
        (RuleKind.VOLUME_TIER, {"rate": Decimal("0.05")}, "in its brackets"),
        (RuleKind.PROMOTION, {"brackets": [Bracket(Decimal(1), Decimal("0.1"))]}, "only volume"),
        (RuleKind.PROMOTION, {"rate": None}, "needs a rate"),
        (RuleKind.PROMOTION, {"rate": Decimal(0)}, "discount"),
        (RuleKind.PROMOTION, {"rate": Decimal("1.0001")}, "discount"),
        (RuleKind.PROMOTION, {"rate": Decimal("0.12345")}, "discount"),
        (RuleKind.PROMOTION, {"rate": Decimal("NaN")}, "discount"),
        (RuleKind.MARGIN_FLOOR, {"rate": Decimal(1)}, "margin floor"),
        (RuleKind.MARGIN_FLOOR, {"rate": Decimal("-0.01")}, "margin floor"),
        (RuleKind.VOLUME_TIER, {"brackets": []}, "1 to"),
        (
            RuleKind.VOLUME_TIER,
            {
                "brackets": [
                    Bracket(Decimal(n), Decimal("0.01")) for n in range(1, MAX_BRACKETS + 2)
                ]
            },
            "1 to",
        ),
        (
            RuleKind.VOLUME_TIER,
            {"brackets": [Bracket(Decimal(10), Decimal("0.05"))] * 2},
            "same quantity",
        ),
        (RuleKind.PROMOTION, {"name": " "}, "name"),
        (RuleKind.PROMOTION, {"name": "x" * 101}, "name"),
    ],
)
def test_pricing_rule_create_rejects_inconsistent_rules(
    kind: RuleKind, fields: RuleFields, message: str
) -> None:
    with pytest.raises(InvalidPricingRuleError, match=message):
        rule(kind, **fields)


def test_volume_brackets_take_valid_quantities() -> None:
    with pytest.raises(InvalidQuantityError):
        rule(RuleKind.VOLUME_TIER, brackets=[Bracket(Decimal(0), Decimal("0.05"))])


def test_rates_cover_everything_off_and_a_zero_margin_floor() -> None:
    assert rule(rate=Decimal(1)).rate == 1
    assert rule(RuleKind.MARGIN_FLOOR, rate=Decimal(0)).rate == 0


def test_pricing_rule_change_edits_terms_and_window_but_not_identity() -> None:
    promotion = rule(product_id=uuid.uuid7())
    scope = (promotion.kind, promotion.product_id, promotion.category_id, promotion.customer_tier)

    promotion.change(name="Extended", rate=Decimal("0.15"), valid_to=NOV_1 + timedelta(days=30))
    floor = rule(RuleKind.MARGIN_FLOOR, valid_to=NOV_1)
    floor.change(valid_to=None, is_active=False)

    assert (promotion.name, promotion.rate, promotion.valid_to) == (
        "Extended",
        Decimal("0.15"),
        datetime(2026, 12, 1, tzinfo=UTC),
    )
    assert (promotion.kind, promotion.product_id, promotion.category_id) == scope[:3]
    assert (floor.valid_to, floor.is_active) == (None, False)


def test_pricing_rule_change_replaces_brackets_and_keeps_what_is_not_sent() -> None:
    tier = rule(RuleKind.VOLUME_TIER)

    tier.change(valid_from=OCT_1 - timedelta(days=1))
    tier.change(brackets=[Bracket(Decimal(50), Decimal("0.08"))])

    assert tier.brackets == (Bracket(Decimal(50), Decimal("0.08")),)
    assert tier.valid_from == datetime(2026, 9, 30, tzinfo=UTC)


def test_pricing_rule_change_rejects_invalid_values_and_changes_nothing() -> None:
    promotion = rule()

    with pytest.raises(InvalidPricingRuleError, match="time-boxed"):
        promotion.change(name="Renamed", rate=Decimal("0.2"), valid_to=None)
    with pytest.raises(InvalidPricingRuleError, match="in its brackets"):
        rule(RuleKind.VOLUME_TIER).change(rate=Decimal("0.1"))

    assert (promotion.name, promotion.rate, promotion.valid_to) == (
        "October promotion",
        Decimal("0.10"),
        NOV_1,
    )


@pytest.mark.parametrize(
    ("at", "effective"),
    [
        (OCT_1 - timedelta(microseconds=1), False),
        (OCT_1, True),
        (NOV_1 - timedelta(microseconds=1), True),
        (NOV_1, False),
    ],
    ids=["before", "from-is-inclusive", "last-instant", "to-is-exclusive"],
)
def test_a_rule_applies_within_its_half_open_window(at: datetime, effective: bool) -> None:
    assert rule().is_effective(at) is effective


def test_an_inactive_or_open_ended_rule() -> None:
    open_ended = rule(RuleKind.MARGIN_FLOOR)
    inactive = rule()
    inactive.change(is_active=False)

    assert open_ended.is_effective(datetime(2099, 1, 1, tzinfo=UTC))
    assert not inactive.is_effective(OCT_1)


def test_a_rule_covers_its_product_its_category_or_every_product() -> None:
    product = bolts()

    assert rule(product_id=product.id).covers(product, CustomerTier.STANDARD)
    assert not rule(product_id=uuid.uuid7()).covers(product, CustomerTier.STANDARD)
    assert rule(category_id=FASTENERS).covers(product, CustomerTier.STANDARD)
    assert not rule(category_id=FASTENERS).covers(bolts(None), CustomerTier.STANDARD)
    assert rule().covers(bolts(None), CustomerTier.STANDARD)


def test_a_customer_tier_rule_covers_only_its_tier() -> None:
    gold = rule(RuleKind.CUSTOMER_TIER, customer_tier=CustomerTier.GOLD)

    assert gold.covers(bolts(), CustomerTier.GOLD)
    assert not gold.covers(bolts(), CustomerTier.SILVER)


def test_a_product_is_more_specific_than_a_category_than_every_product() -> None:
    assert [
        rule(product_id=uuid.uuid7()).specificity,
        rule(category_id=FASTENERS).specificity,
        rule().specificity,
    ] == [2, 1, 0]
