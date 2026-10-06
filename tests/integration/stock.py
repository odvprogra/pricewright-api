"""A tenant with people, a customer, two products and two rules, saved through the real unit of
work: what quotes and orders in PostgreSQL are built from."""

import dataclasses
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from pricewright.domain.actors import Actor
from pricewright.domain.catalog import Product, UnitOfMeasure
from pricewright.domain.customers import Customer, CustomerTier
from pricewright.domain.money import Money
from pricewright.domain.pricing import PriceOverride, RateOverride
from pricewright.domain.pricing_rules import Bracket, PricingRule, RuleKind
from pricewright.domain.quotes import LineChange, PricingContext, Quote
from pricewright.domain.tenants import Tenant
from pricewright.domain.users import User
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import Sessions, register

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)


@dataclasses.dataclass(frozen=True)
class Stock:
    tenant: Tenant
    rep: User
    manager: User
    director: User
    """Neither creates nor edits quotes here: decides on approvals (ADR-0020)."""
    customer: Customer
    bolts: Product
    gloves: Product
    rules: tuple[PricingRule, ...]

    def pricing(self, at: datetime = NOW) -> PricingContext:
        return PricingContext(
            customer=self.customer,
            settings=self.tenant.settings,
            rules=self.rules,
            products={self.bolts.id: self.bolts, self.gloves.id: self.gloves},
            at=at,
        )


def usd(amount: str) -> Money:
    return Money(Decimal(amount), "USD")


async def stock(sessions: Sessions, name: str = "Northfield") -> Stock:
    domain = f"{name.lower()}.example"
    tenant, (rep, manager, director) = await register(
        sessions, name, f"rep@{domain}", f"manager@{domain}", f"director@{domain}"
    )
    customer = Customer.create(
        tenant_id=tenant.id, account_number="C-1001", name="Acme", tier=CustomerTier.GOLD
    )
    bolts, gloves = (
        Product.create(
            tenant_id=tenant.id,
            currency="USD",
            sku=sku,
            name=f"Product {sku}",
            unit=UnitOfMeasure.EACH,
            list_price=usd(price),
            unit_cost=usd(cost),
        )
        for sku, price, cost in (("FAS-M6-100", "100", "60"), ("PPE-GLV-L", "12.5", "5"))
    )
    rules = (
        PricingRule.create(
            tenant_id=tenant.id,
            kind=RuleKind.MARGIN_FLOOR,
            name="Keep 20%",
            rate=Decimal("0.2"),
            valid_from=NOW - timedelta(days=30),
        ),
        PricingRule.create(
            tenant_id=tenant.id,
            kind=RuleKind.VOLUME_TIER,
            name="Volume",
            valid_from=NOW - timedelta(days=30),
            brackets=[Bracket(Decimal(10), Decimal("0.1"))],
        ),
    )
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(tenant.id)
        await uow.customers.add(customer)
        await uow.products.add(bolts)
        await uow.products.add(gloves)
        for rule in rules:
            await uow.pricing_rules.add(rule)
        await uow.commit()
    return Stock(tenant, rep, manager, director, customer, bolts, gloves, rules)


def draft(stock: Stock, number: str = "NF-2026-000001") -> Quote:
    rep, manager = Actor.person(stock.rep.id), Actor.person(stock.manager.id)
    quote = Quote.draft(
        number=number,
        valid_until=date(2026, 11, 14),
        by=rep,
        context=stock.pricing(),
        notes="Delivery in two weeks.",
        lines=[LineChange(stock.bolts.id, Decimal("12.5"))],
    )
    quote.add_line(
        LineChange(stock.gloves.id, Decimal(3), RateOverride(Decimal("0.3"), "Clearance")),
        by=manager,
        context=stock.pricing(),
    )
    quote.add_line(
        # 70 keeps a 14% margin, below the 20% floor: submitting asks for approval.
        LineChange(stock.bolts.id, Decimal(1), PriceOverride(usd("70"), "Agreed price")),
        by=manager,
        context=stock.pricing(),
    )
    return quote


async def accepted(sessions: Sessions, stock: Stock, number: str = "NF-2026-000001") -> Quote:
    """A stored quote, approved by the director (a line is below its floor), sent and accepted."""
    quote = draft(stock, number)
    quote.submit(by=Actor.person(stock.rep.id), context=stock.pricing())
    quote.approve(by=Actor.person(stock.director.id), now=NOW)
    quote.send(now=NOW)
    quote.accept(now=NOW)
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(stock.tenant.id)
        await uow.quotes.add(quote)
        await uow.commit()
    return quote
