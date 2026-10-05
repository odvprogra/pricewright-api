"""A small Northfield catalog and the people who quote it, for the quote aggregate's tests."""

import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from pricewright.domain.audit import ActorType
from pricewright.domain.catalog import Product, UnitOfMeasure
from pricewright.domain.customers import Customer, CustomerTier
from pricewright.domain.money import Money
from pricewright.domain.pricing_rules import Bracket, PricingRule, RuleKind
from pricewright.domain.quotes import Actor, LineChange, PricingContext, Quote
from pricewright.domain.tenants import TenantSettings

TENANT = uuid.uuid7()
NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
IN_A_MONTH = date(2026, 11, 14)
SETTINGS = TenantSettings(currency="USD", tax_rate=Decimal("0.0725"), quote_prefix="NF")

REP = Actor(ActorType.USER, uuid.uuid7())
OTHER_REP = Actor(ActorType.USER, uuid.uuid7())
MANAGER = Actor(ActorType.USER, uuid.uuid7())
INTEGRATION = Actor(ActorType.SERVICE_ACCOUNT, uuid.uuid7())


def usd(amount: str) -> Money:
    return Money(Decimal(amount), "USD")


def product(sku: str, list_price: str, unit_cost: str) -> Product:
    return Product.create(
        tenant_id=TENANT,
        currency="USD",
        sku=sku,
        name=f"Product {sku}",
        unit=UnitOfMeasure.EACH,
        list_price=usd(list_price),
        unit_cost=usd(unit_cost),
    )


BOLTS = product("FAS-M6-100", "100", "60")
GLOVES = product("PPE-GLV-L", "12.5", "5")
ACME = Customer.create(
    tenant_id=TENANT, account_number="C-1001", name="Acme", tier=CustomerTier.GOLD
)


def volume_tier(*brackets: tuple[str, str]) -> PricingRule:
    return PricingRule.create(
        tenant_id=TENANT,
        kind=RuleKind.VOLUME_TIER,
        name="Volume",
        valid_from=NOW - timedelta(days=30),
        brackets=[Bracket(Decimal(units), Decimal(rate)) for units, rate in brackets],
    )


def margin_floor(rate: str) -> PricingRule:
    return PricingRule.create(
        tenant_id=TENANT,
        kind=RuleKind.MARGIN_FLOOR,
        name="Keep 20%",
        rate=Decimal(rate),
        valid_from=NOW - timedelta(days=30),
    )


def context(
    *,
    at: datetime = NOW,
    rules: Iterable[PricingRule] = (),
    products: Iterable[Product] = (BOLTS, GLOVES),
    settings: TenantSettings = SETTINGS,
    customer: Customer = ACME,
) -> PricingContext:
    return PricingContext(
        customer=customer,
        settings=settings,
        rules=tuple(rules),
        products={item.id: item for item in products},
        at=at,
    )


def draft(
    *lines: LineChange,
    by: Actor = REP,
    pricing: PricingContext | None = None,
    valid_until: date = IN_A_MONTH,
) -> Quote:
    return Quote.draft(
        number="NF-2026-000001",
        valid_until=valid_until,
        by=by,
        context=pricing or context(),
        lines=lines,
    )
