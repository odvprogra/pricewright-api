import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from pricewright.application.pagination import Keyset
from pricewright.application.ports import PricingRuleQuery, PricingRuleSort, UnitOfWork
from pricewright.application.pricing_rules import (
    NewPricingRule,
    PricingRuleChanges,
    change_pricing_rule,
    create_pricing_rule,
    get_pricing_rule,
    list_pricing_rules,
)
from pricewright.domain.audit import AuditAction
from pricewright.domain.auth import Permission, PermissionDeniedError, Principal
from pricewright.domain.catalog import (
    Product,
    ProductCategory,
    UnitOfMeasure,
    UnknownCategoryError,
    UnknownProductError,
)
from pricewright.domain.customers import CustomerTier
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.money import Money
from pricewright.domain.pricing_rules import Bracket, InvalidPricingRuleError, PricingRule, RuleKind
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeClock, FakeUnitOfWork, InMemoryDatabase

NOV_1 = datetime(2026, 11, 1, tzinfo=UTC)


def volume(
    *, product_id: uuid.UUID | None = None, category_id: uuid.UUID | None = None
) -> NewPricingRule:
    return NewPricingRule(
        kind=RuleKind.VOLUME_TIER,
        name="Fasteners in bulk",
        brackets=(Bracket(Decimal(100), Decimal("0.1")), Bracket(Decimal(10), Decimal("0.05"))),
        product_id=product_id,
        category_id=category_id,
    )


def promotion(name: str = "October") -> NewPricingRule:
    return NewPricingRule(kind=RuleKind.PROMOTION, name=name, rate=Decimal("0.125"), valid_to=NOV_1)


class Fixture:
    def __init__(self) -> None:
        self.northfield = Tenant.register(
            name="Northfield", settings=TenantSettings("USD", Decimal(0))
        )
        self.larkspur = Tenant.register(name="Larkspur", settings=TenantSettings("USD", Decimal(0)))
        self.fasteners = ProductCategory.create(tenant_id=self.northfield.id, name="Fasteners")
        self.their_product = Product.create(
            tenant_id=self.larkspur.id,
            currency="USD",
            sku="LT-1",
            name="Their product",
            unit=UnitOfMeasure.EACH,
            list_price=Money(Decimal(1), "USD"),
            unit_cost=Money(Decimal(1), "USD"),
        )
        self.database = InMemoryDatabase(
            tenants={self.northfield.id: self.northfield, self.larkspur.id: self.larkspur},
            product_categories={self.fasteners.id: self.fasteners},
            products={self.their_product.id: self.their_product},
        )
        self.clock = FakeClock()
        self.manager = Principal(self.northfield.id, uuid.uuid7(), Role.SALES_MANAGER)

    def unit_of_work(self) -> UnitOfWork:
        return FakeUnitOfWork(self.database)

    async def add(self, new: NewPricingRule, caller: Principal | None = None) -> PricingRule:
        return await create_pricing_rule(
            caller or self.manager, new, unit_of_work=self.unit_of_work, clock=self.clock
        )

    async def change(
        self, rule: PricingRule, changes: PricingRuleChanges, version: int = 1
    ) -> PricingRule:
        return await change_pricing_rule(
            self.manager,
            rule.id,
            changes,
            expected_version=version,
            unit_of_work=self.unit_of_work,
            clock=self.clock,
        )


async def test_a_manager_adds_a_rule_from_now_and_the_trail_records_it() -> None:
    fixture = Fixture()

    rule = await fixture.add(volume(category_id=fixture.fasteners.id))

    assert fixture.database.pricing_rules[rule.id] == rule
    assert rule.valid_from == fixture.clock.now
    [event] = fixture.database.audit_events.values()
    assert (event.action, event.resource_id) == (AuditAction.PRICING_RULE_CREATED, rule.id)
    assert event.changes == {
        "kind": (None, "volume_tier"),
        "name": (None, "Fasteners in bulk"),
        "category_id": (None, str(fixture.fasteners.id)),
        "brackets": (None, "10.000:0.0500 100.000:0.1000"),
        "valid_from": (None, fixture.clock.now.isoformat()),
        "is_active": (None, True),
    }


@pytest.mark.parametrize(
    ("scope", "error"),
    [
        ("missing product", UnknownProductError),
        ("their product", UnknownProductError),
        ("missing category", UnknownCategoryError),
    ],
)
async def test_a_rule_is_about_a_product_or_category_of_its_tenant(
    scope: str, error: type[Exception]
) -> None:
    fixture = Fixture()
    new = {
        "missing product": volume(product_id=uuid.uuid7()),
        "their product": volume(product_id=fixture.their_product.id),
        "missing category": volume(category_id=uuid.uuid7()),
    }[scope]

    with pytest.raises(error):
        await fixture.add(new)

    assert fixture.database.pricing_rules == {}


async def test_an_inconsistent_rule_is_refused() -> None:
    fixture = Fixture()

    with pytest.raises(InvalidPricingRuleError, match="tier"):
        await fixture.add(
            NewPricingRule(kind=RuleKind.CUSTOMER_TIER, name="Gold", rate=Decimal("0.05"))
        )


async def test_everyone_reads_the_rules_but_reps_and_integrations_cannot_add_them() -> None:
    fixture = Fixture()
    rule = await fixture.add(promotion())
    rep = Principal(fixture.northfield.id, uuid.uuid7(), Role.SALES_REP)
    integration = Principal(
        fixture.northfield.id, uuid.uuid7(), scopes=frozenset({Permission.PRICING_READ})
    )

    found = await get_pricing_rule(rep, rule.id, unit_of_work=fixture.unit_of_work)
    listed = await list_pricing_rules(
        integration, PricingRuleQuery(), after=None, limit=10, unit_of_work=fixture.unit_of_work
    )
    for caller in (rep, integration):
        with pytest.raises(PermissionDeniedError, match="pricing:manage"):
            await fixture.add(promotion("Mine"), caller)

    assert found == rule
    assert listed.items == [rule]


async def test_rules_are_listed_by_the_requested_order_and_filters() -> None:
    fixture = Fixture()
    for name in ["Weekend", "autumn", "Clearance"]:
        await fixture.add(promotion(name))
    gold = await fixture.add(
        NewPricingRule(
            kind=RuleKind.CUSTOMER_TIER,
            name="Gold customers",
            rate=Decimal("0.05"),
            customer_tier=CustomerTier.GOLD,
        )
    )
    seen: list[str] = []
    after: Keyset | None = None
    while True:
        page = await list_pricing_rules(
            fixture.manager,
            PricingRuleQuery(kind=RuleKind.PROMOTION, sort=PricingRuleSort.NAME),
            after=after,
            limit=2,
            unit_of_work=fixture.unit_of_work,
        )
        seen += [rule.name for rule in page.items]
        if (after := page.next_after) is None:
            break
    by_tier = await list_pricing_rules(
        fixture.manager,
        PricingRuleQuery(customer_tier=CustomerTier.GOLD, sort=PricingRuleSort.CREATED),
        after=None,
        limit=10,
        unit_of_work=fixture.unit_of_work,
    )

    assert seen == ["autumn", "Clearance", "Weekend"]
    assert by_tier.items == [gold]


async def test_a_rule_is_edited_with_a_new_version_and_an_audit_event() -> None:
    fixture = Fixture()
    rule = await fixture.add(promotion())
    fixture.clock.advance(timedelta(hours=1))

    changed = await fixture.change(
        rule,
        PricingRuleChanges(
            rate=Decimal("0.15"), valid_to=NOV_1 + timedelta(days=7), name="October"
        ),
    )

    assert (changed.version, changed.rate) == (2, Decimal("0.15"))
    _, edit = sorted(fixture.database.audit_events.values(), key=lambda event: event.id)
    assert (edit.action, edit.occurred_at) == (AuditAction.PRICING_RULE_UPDATED, fixture.clock.now)
    assert edit.changes == {
        "rate": ("0.1250", "0.1500"),
        "valid_to": (NOV_1.isoformat(), "2026-11-08T00:00:00+00:00"),
    }


async def test_an_edit_that_changes_nothing_records_no_event() -> None:
    fixture = Fixture()
    rule = await fixture.add(volume())

    await fixture.change(
        rule,
        PricingRuleChanges(
            brackets=(
                Bracket(Decimal("10.0"), Decimal("0.050")),
                Bracket(Decimal(100), Decimal("0.1")),
            )
        ),
    )

    assert len(fixture.database.audit_events) == 1


async def test_a_floor_loses_its_end_and_a_rule_is_deactivated() -> None:
    fixture = Fixture()
    floor = await fixture.add(
        NewPricingRule(
            kind=RuleKind.MARGIN_FLOOR, name="Floor", rate=Decimal("0.2"), valid_to=NOV_1
        )
    )

    changed = await fixture.change(floor, PricingRuleChanges(valid_to=None, is_active=False))

    assert (changed.valid_to, changed.is_active) == (None, False)


async def test_editing_from_an_old_version_is_rejected() -> None:
    fixture = Fixture()
    rule = await fixture.add(promotion())
    await fixture.change(rule, PricingRuleChanges(name="First"))

    with pytest.raises(StaleVersionError):
        await fixture.change(rule, PricingRuleChanges(name="Second"), version=1)


async def test_another_tenants_rule_is_not_found() -> None:
    fixture = Fixture()
    rule = await fixture.add(promotion())
    outsider = Principal(fixture.larkspur.id, uuid.uuid7(), Role.ADMIN)

    with pytest.raises(NotFoundError):
        await get_pricing_rule(outsider, rule.id, unit_of_work=fixture.unit_of_work)
    with pytest.raises(NotFoundError):
        await change_pricing_rule(
            outsider,
            rule.id,
            PricingRuleChanges(is_active=False),
            expected_version=1,
            unit_of_work=fixture.unit_of_work,
            clock=fixture.clock,
        )
