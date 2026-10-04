import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from pricewright.application.ports import UnitOfWork
from pricewright.application.pricing import PreviewLine, PricePreview, preview_prices
from pricewright.domain.auth import Permission, PermissionDeniedError, Principal
from pricewright.domain.catalog import Product, UnitOfMeasure, UnknownProductError
from pricewright.domain.customers import Customer, CustomerTier, UnknownCustomerError
from pricewright.domain.errors import NotFoundError
from pricewright.domain.money import Money
from pricewright.domain.pricing import ArchivedProductError, Stage
from pricewright.domain.pricing_rules import PricingRule, RuleKind
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeClock, FakeUnitOfWork, InMemoryDatabase


def usd(amount: str) -> Money:
    return Money(Decimal(amount), "USD")


class Fixture:
    def __init__(self) -> None:
        self.clock = FakeClock()
        self.northfield = Tenant.register(
            name="Northfield", settings=TenantSettings("USD", Decimal("0.0725"))
        )
        self.larkspur = Tenant.register(name="Larkspur", settings=TenantSettings("USD", Decimal(0)))
        self.bolts = self.product(self.northfield)
        self.their_product = self.product(self.larkspur)
        self.acme = Customer.create(
            tenant_id=self.northfield.id,
            account_number="C-1001",
            name="Acme",
            tier=CustomerTier.GOLD,
        )
        self.next_week = self.clock.now + timedelta(days=7)
        self.rules = [
            self.rule(RuleKind.CUSTOMER_TIER, rate="0.05", customer_tier=CustomerTier.GOLD),
            self.rule(RuleKind.PROMOTION, rate="0.1", valid_from=self.next_week),
        ]
        self.database = InMemoryDatabase(
            tenants={tenant.id: tenant for tenant in (self.northfield, self.larkspur)},
            products={product.id: product for product in (self.bolts, self.their_product)},
            customers={self.acme.id: self.acme},
            pricing_rules={rule.id: rule for rule in self.rules},
        )
        self.rep = Principal(self.northfield.id, uuid.uuid7(), Role.SALES_REP)

    def product(self, tenant: Tenant) -> Product:
        return Product.create(
            tenant_id=tenant.id,
            currency="USD",
            sku="FAS-M6-100",
            name="Hex bolt",
            unit=UnitOfMeasure.BOX,
            list_price=usd("100"),
            unit_cost=usd("60"),
        )

    def rule(
        self,
        kind: RuleKind,
        *,
        rate: str,
        customer_tier: CustomerTier | None = None,
        valid_from: datetime | None = None,
    ) -> PricingRule:
        start = self.clock.now if valid_from is None else valid_from
        return PricingRule.create(
            tenant_id=self.northfield.id,
            kind=kind,
            name=f"{kind} rule",
            rate=Decimal(rate),
            valid_from=start,
            valid_to=start + timedelta(days=30) if kind is RuleKind.PROMOTION else None,
            customer_tier=customer_tier,
        )

    def unit_of_work(self) -> UnitOfWork:
        return FakeUnitOfWork(self.database)

    async def preview(
        self,
        *lines: PreviewLine,
        customer_id: uuid.UUID | None = None,
        caller: Principal | None = None,
        days_ahead: int | None = None,
    ) -> PricePreview:
        return await preview_prices(
            caller or self.rep,
            customer_id or self.acme.id,
            lines or [PreviewLine(self.bolts.id, Decimal(10))],
            priced_at=None if days_ahead is None else self.clock.now + timedelta(days=days_ahead),
            unit_of_work=self.unit_of_work,
            clock=self.clock,
        )


async def test_a_preview_prices_with_the_rules_effective_now() -> None:
    fixture = Fixture()

    result = await fixture.preview()

    [line] = result.quote.lines
    assert [step.stage for step in line.breakdown.steps] == [Stage.CUSTOMER_TIER]
    assert (line.net_total, result.quote.tax, result.quote.total) == (
        usd("950"),
        usd("68.88"),
        usd("1018.88"),
    )
    assert result.priced_at == fixture.clock.now
    assert result.products == {fixture.bolts.id: fixture.bolts}


async def test_a_preview_can_price_at_a_later_time() -> None:
    fixture = Fixture()

    result = await fixture.preview(days_ahead=7)

    stages = [step.stage for step in result.quote.lines[0].breakdown.steps]
    assert stages == [Stage.CUSTOMER_TIER, Stage.PROMOTION]
    assert result.priced_at == fixture.next_week


async def test_an_integration_with_pricing_read_previews_prices() -> None:
    fixture = Fixture()
    integration = Principal(
        fixture.northfield.id, uuid.uuid7(), scopes=frozenset({Permission.PRICING_READ})
    )
    without = Principal(
        fixture.northfield.id, uuid.uuid7(), scopes=frozenset({Permission.CATALOG_READ})
    )

    result = await fixture.preview(caller=integration)

    assert result.quote.net_subtotal == usd("950")
    with pytest.raises(PermissionDeniedError, match="pricing:read"):
        await fixture.preview(caller=without)


@pytest.mark.parametrize("which", ["missing", "another tenant's"])
async def test_the_customer_must_be_one_of_the_tenant(which: str) -> None:
    fixture = Fixture()
    theirs = Customer.create(tenant_id=fixture.larkspur.id, account_number="X-1", name="Theirs")
    fixture.database.customers[theirs.id] = theirs

    with pytest.raises(UnknownCustomerError):
        await fixture.preview(customer_id=uuid.uuid7() if which == "missing" else theirs.id)


async def test_every_product_must_be_one_of_the_tenant() -> None:
    fixture = Fixture()
    lines = (
        PreviewLine(fixture.bolts.id, Decimal(1)),
        PreviewLine(fixture.their_product.id, Decimal(1)),
    )

    with pytest.raises(UnknownProductError, match=str(fixture.their_product.id)):
        await fixture.preview(*lines)


async def test_a_product_may_appear_on_several_lines_but_not_once_archived() -> None:
    fixture = Fixture()
    twice = (PreviewLine(fixture.bolts.id, Decimal(1)), PreviewLine(fixture.bolts.id, Decimal(2)))

    result = await fixture.preview(*twice)
    fixture.database.products[fixture.bolts.id].change(is_active=False)

    assert [line.quantity for line in result.quote.lines] == [1, 2]
    with pytest.raises(ArchivedProductError):
        await fixture.preview(*twice)


async def test_a_tenant_that_no_longer_exists_has_no_prices() -> None:
    fixture = Fixture()
    gone = Principal(uuid.uuid7(), uuid.uuid7(), Role.SALES_REP)

    with pytest.raises(NotFoundError, match="tenant"):
        await fixture.preview(caller=gone)
