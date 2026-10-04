"""Pricing rules in PostgreSQL: brackets, windows, filters and keyset pages, and the keys and checks
that keep each rule in its tenant and in its kind's shape (ADR-0006, ADR-0018)."""

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from pricewright.application.pagination import Keyset
from pricewright.application.ports import PricingRuleQuery, PricingRuleSort
from pricewright.domain.catalog import Product, ProductCategory, UnitOfMeasure
from pricewright.domain.customers import CustomerTier
from pricewright.domain.errors import StaleVersionError
from pricewright.domain.money import Money
from pricewright.domain.pricing_rules import Bracket, PricingRule, RuleKind
from pricewright.domain.tenants import Tenant
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import Sessions, register

pytestmark = pytest.mark.integration

OCT_1 = datetime(2026, 10, 1, tzinfo=UTC)
NOV_1 = datetime(2026, 11, 1, tzinfo=UTC)


def volume_tier(
    tenant: Tenant, name: str = "Volume", *, product_id: uuid.UUID | None = None
) -> PricingRule:
    return PricingRule.create(
        tenant_id=tenant.id,
        kind=RuleKind.VOLUME_TIER,
        name=name,
        valid_from=OCT_1,
        brackets=[
            Bracket(Decimal("100"), Decimal("0.1")),
            Bracket(Decimal("12.5"), Decimal("0.0375")),
        ],
        product_id=product_id,
    )


def promotion(tenant: Tenant, name: str, *, valid_to: datetime = NOV_1) -> PricingRule:
    return PricingRule.create(
        tenant_id=tenant.id,
        kind=RuleKind.PROMOTION,
        name=name,
        rate=Decimal("0.125"),
        valid_from=OCT_1,
        valid_to=valid_to,
    )


def gold_discount(tenant: Tenant, category: ProductCategory) -> PricingRule:
    return PricingRule.create(
        tenant_id=tenant.id,
        kind=RuleKind.CUSTOMER_TIER,
        name="Gold fasteners",
        rate=Decimal("0.05"),
        valid_from=OCT_1,
        category_id=category.id,
        customer_tier=CustomerTier.GOLD,
    )


def margin_floor(tenant: Tenant) -> PricingRule:
    return PricingRule.create(
        tenant_id=tenant.id,
        kind=RuleKind.MARGIN_FLOOR,
        name="Floor",
        rate=Decimal(0),
        valid_from=OCT_1,
    )


def bolts(tenant: Tenant) -> Product:
    return Product.create(
        tenant_id=tenant.id,
        currency="USD",
        sku="BOLT-M6",
        name="Hex bolt",
        unit=UnitOfMeasure.BOX,
        list_price=Money(Decimal(12), "USD"),
        unit_cost=Money(Decimal(7), "USD"),
    )


async def store(
    sessions: Sessions, tenant: Tenant, *items: PricingRule | Product | ProductCategory
) -> None:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(tenant.id)
        for item in items:
            match item:
                case PricingRule():
                    await uow.pricing_rules.add(item)
                case Product():
                    await uow.products.add(item)
                case ProductCategory():
                    await uow.product_categories.add(item)
        await uow.commit()


async def test_rules_of_every_kind_round_trip_with_their_brackets(
    session_factory: Sessions,
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    fasteners = ProductCategory.create(tenant_id=northfield.id, name="Fasteners")
    product = bolts(northfield)
    rules = [
        volume_tier(northfield, product_id=product.id),
        gold_discount(northfield, fasteners),
        promotion(northfield, "October"),
        margin_floor(northfield),
    ]
    await store(session_factory, northfield, fasteners, product)
    await store(session_factory, northfield, *rules)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        found = [await uow.pricing_rules.get(rule.id) for rule in rules]

    assert found == rules
    assert found[0] is not None
    assert [str(b.min_quantity) for b in found[0].brackets] == ["12.500", "100.000"]


async def test_saving_bumps_the_version_and_replaces_the_brackets(
    session_factory: Sessions,
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    tier = volume_tier(northfield)
    await store(session_factory, northfield, tier)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        loaded = await uow.pricing_rules.get(tier.id)
        assert loaded is not None
        loaded.change(name="Bulk", brackets=[Bracket(Decimal(100), Decimal("0.12"))])
        await uow.pricing_rules.save(loaded)
        await uow.commit()
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        saved = await uow.pricing_rules.get(tier.id)
        with pytest.raises(StaleVersionError):
            await uow.pricing_rules.save(tier)  # still at version 1

    assert saved == dataclasses.replace(
        tier, name="Bulk", brackets=(Bracket(Decimal(100), Decimal("0.12")),), version=2
    )


async def test_effective_rules_are_active_within_their_half_open_window(
    session_factory: Sessions,
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    october, ending = (
        promotion(northfield, "October"),
        promotion(northfield, "Ending", valid_to=OCT_1 + timedelta(days=1)),
    )
    inactive, floor = promotion(northfield, "Inactive"), margin_floor(northfield)
    inactive.change(is_active=False)
    await store(session_factory, northfield, october, ending, inactive, floor)

    async def effective(at: datetime) -> list[str]:
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            uow.bind_tenant(northfield.id)
            return [rule.name for rule in await uow.pricing_rules.effective_at(at)]

    assert await effective(OCT_1 - timedelta(microseconds=1)) == []
    assert await effective(OCT_1) == ["October", "Ending", "Floor"]
    assert await effective(OCT_1 + timedelta(days=1)) == ["October", "Floor"]
    assert await effective(NOV_1) == ["Floor"]


async def test_rules_are_filtered_sorted_and_paged(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    fasteners = ProductCategory.create(tenant_id=northfield.id, name="Fasteners")
    product = bolts(northfield)
    tier, gold = (
        volume_tier(northfield, "volume", product_id=product.id),
        gold_discount(northfield, fasteners),
    )
    archived = promotion(northfield, "Archived")
    archived.change(is_active=False)
    await store(session_factory, northfield, fasteners, product)
    await store(session_factory, northfield, tier, gold, archived)

    async def names(query: PricingRuleQuery, after: Keyset | None = None) -> list[str]:
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            uow.bind_tenant(northfield.id)
            return [
                rule.name for rule in await uow.pricing_rules.page(query, after=after, limit=10)
            ]

    assert await names(PricingRuleQuery()) == ["Archived", "Gold fasteners", "volume"]
    assert await names(PricingRuleQuery(sort=PricingRuleSort.CREATED, descending=True)) == [
        "Archived",
        "Gold fasteners",
        "volume",
    ]
    assert await names(PricingRuleQuery(), after=Keyset(gold.id, gold.name)) == ["volume"]
    assert await names(PricingRuleQuery(kind=RuleKind.VOLUME_TIER)) == ["volume"]
    assert await names(PricingRuleQuery(product_id=product.id)) == ["volume"]
    assert await names(PricingRuleQuery(category_id=fasteners.id)) == ["Gold fasteners"]
    assert await names(PricingRuleQuery(customer_tier=CustomerTier.GOLD)) == ["Gold fasteners"]
    assert await names(PricingRuleQuery(active=False)) == ["Archived"]


async def test_rules_stay_inside_their_tenant(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    larkspur, _ = await register(session_factory, "Larkspur")
    rule = margin_floor(northfield)
    await store(session_factory, northfield, rule)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(larkspur.id)
        found = await uow.pricing_rules.get(rule.id)
        listed = await uow.pricing_rules.page(PricingRuleQuery(), after=None, limit=10)
        effective = await uow.pricing_rules.effective_at(NOV_1)
        with pytest.raises(RuntimeError, match="tenant"):
            await uow.pricing_rules.add(margin_floor(northfield))
        with pytest.raises(RuntimeError, match="tenant"):
            await uow.pricing_rules.save(rule)

    assert (found, listed, effective) == (None, [], [])


async def test_a_rule_cannot_point_at_another_tenants_product(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    larkspur, _ = await register(session_factory, "Larkspur")
    product = bolts(northfield)
    await store(session_factory, northfield, product)

    with pytest.raises(IntegrityError, match="fk_pricing_rules_tenant_id_product_id_products"):
        await store(session_factory, larkspur, volume_tier(larkspur, product_id=product.id))


@pytest.mark.parametrize(
    ("change", "constraint"),
    [
        ({"customer_tier": CustomerTier.GOLD}, "customer_tier_only_on_tier_discounts"),
        ({"rate": None}, "rate_unless_volume_tier"),
        ({"rate": Decimal(0)}, "rate_in_range"),
        ({"valid_to": None}, "promotions_end"),
        ({"valid_to": OCT_1}, "window_is_ordered"),
        ({"product_id": uuid.uuid7(), "category_id": uuid.uuid7()}, "one_scope"),
    ],
)
async def test_the_database_holds_each_kinds_shape(
    session_factory: Sessions, change: dict[str, object], constraint: str
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    broken = promotion(northfield, "Broken")
    for field, value in change.items():  # bypasses the domain's checks, to reach the database's
        setattr(broken, field, value)

    with pytest.raises(IntegrityError, match=constraint):
        await store(session_factory, northfield, broken)
