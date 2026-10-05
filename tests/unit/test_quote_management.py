"""Creating and reading quotes through the use cases, with in-memory fakes."""

import uuid
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

import pytest

from pricewright.application.ports import UnitOfWork
from pricewright.application.quotes import NewQuote, create_quote, get_quote
from pricewright.domain.actors import Actor
from pricewright.domain.audit import AuditAction
from pricewright.domain.auth import Permission, PermissionDeniedError, Principal
from pricewright.domain.catalog import Product, UnitOfMeasure, UnknownProductError
from pricewright.domain.customers import Customer, CustomerTier, UnknownCustomerError
from pricewright.domain.errors import NotFoundError
from pricewright.domain.money import Money
from pricewright.domain.pricing import ArchivedCustomerError, Stage
from pricewright.domain.pricing_rules import Bracket, PricingRule, RuleKind
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.quotes import InvalidQuoteError, LineChange, Quote
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeClock, FakeUnitOfWork, InMemoryDatabase


def usd(amount: str) -> Money:
    return Money(Decimal(amount), "USD")


class Fixture:
    def __init__(self) -> None:
        self.clock = FakeClock()  # 2026-10-03 12:00 UTC
        self.northfield = Tenant.register(
            name="Northfield",
            settings=TenantSettings("USD", Decimal("0.0725"), quote_prefix="NF"),
        )
        self.larkspur = Tenant.register(
            name="Larkspur", settings=TenantSettings("USD", Decimal(0), quote_prefix="LT")
        )
        self.bolts = self.product(self.northfield)
        self.their_product = self.product(self.larkspur)
        self.acme = Customer.create(
            tenant_id=self.northfield.id,
            account_number="C-1001",
            name="Acme",
            tier=CustomerTier.GOLD,
        )
        self.their_customer = Customer.create(
            tenant_id=self.larkspur.id, account_number="C-1001", name="Theirs"
        )
        volume = PricingRule.create(
            tenant_id=self.northfield.id,
            kind=RuleKind.VOLUME_TIER,
            name="Bulk",
            valid_from=self.clock.now - timedelta(days=1),
            brackets=[Bracket(Decimal(10), Decimal("0.1"))],
        )
        self.database = InMemoryDatabase(
            tenants={tenant.id: tenant for tenant in (self.northfield, self.larkspur)},
            products={product.id: product for product in (self.bolts, self.their_product)},
            customers={c.id: c for c in (self.acme, self.their_customer)},
            pricing_rules={volume.id: volume},
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

    def unit_of_work(self) -> UnitOfWork:
        return FakeUnitOfWork(self.database)

    def integration(self, *scopes: Permission) -> Principal:
        return Principal(self.northfield.id, uuid.uuid7(), scopes=frozenset(scopes))

    async def create(self, new: NewQuote | None = None, caller: Principal | None = None) -> Quote:
        return await create_quote(
            caller or self.rep,
            new or NewQuote(self.acme.id, lines=[LineChange(self.bolts.id, Decimal(10))]),
            unit_of_work=self.unit_of_work,
            clock=self.clock,
        )


@pytest.fixture
def f() -> Fixture:
    return Fixture()


async def test_create_quote_numbers_drafts_per_tenant_and_year(f: Fixture) -> None:
    first = await f.create()
    second = await f.create()

    assert (first.number, second.number) == ("NF-2026-000001", "NF-2026-000002")
    assert f.database.quote_numbers == {(f.northfield.id, 2026): 2}


async def test_create_quote_prices_its_lines_now_and_stores_it(f: Fixture) -> None:
    quote = await f.create()

    assert (quote.status, quote.created_by) == (QuoteStatus.DRAFT, Actor.of(f.rep))
    assert quote.created_at == f.clock.now
    [line] = quote.lines
    assert [step.stage for step in line.pricing.breakdown.steps] == [Stage.VOLUME_TIER]
    assert quote.totals.net_subtotal == usd("900.00")
    assert f.database.quotes[quote.id] == quote


async def test_create_quote_is_valid_for_the_tenants_days_by_default(f: Fixture) -> None:
    defaulted = await f.create()
    chosen = await f.create(NewQuote(f.acme.id, valid_until=date(2026, 12, 31)))

    assert defaulted.valid_until == date(2026, 11, 2)  # October 3 + 30 days
    assert chosen.valid_until == date(2026, 12, 31)


async def test_create_quote_records_its_audit_event(f: Fixture) -> None:
    quote = await f.create(NewQuote(f.acme.id, notes="Rush order"))

    [event] = f.database.audit_events.values()
    assert (event.action, event.resource_id, event.actor_id) == (
        AuditAction.QUOTE_CREATED,
        quote.id,
        f.rep.subject_id,
    )
    assert event.changes == {
        "number": (None, "NF-2026-000001"),
        "customer_id": (None, str(f.acme.id)),
        "status": (None, "draft"),
        "valid_until": (None, "2026-11-02"),
        "notes": (None, "Rush order"),
        "lines": (None, 0),
        "net_subtotal": (None, "0.0000"),
        "tax": (None, "0.0000"),
        "total": (None, "0.0000"),
    }


async def test_an_integration_with_quotes_manage_creates_drafts(f: Fixture) -> None:
    integration = f.integration(Permission.QUOTES_MANAGE)

    quote = await f.create(caller=integration)

    assert quote.created_by == Actor.of(integration)
    assert not quote.created_by.is_person


async def test_create_quote_needs_quotes_manage(f: Fixture) -> None:
    with pytest.raises(PermissionDeniedError, match="quotes:manage"):
        await f.create(caller=f.integration(Permission.QUOTES_READ))

    assert f.database.quotes == {}


@pytest.mark.parametrize(
    "new",
    [
        lambda f: NewQuote(f.their_customer.id),
        lambda f: NewQuote(f.acme.id, lines=[LineChange(f.their_product.id, Decimal(1))]),
        lambda f: NewQuote(f.acme.id, valid_until=date(2026, 10, 2)),
    ],
    ids=["their-customer", "their-product", "past-date"],
)
async def test_a_refused_quote_takes_no_number(
    f: Fixture, new: Callable[[Fixture], NewQuote]
) -> None:
    with pytest.raises((UnknownCustomerError, UnknownProductError, InvalidQuoteError)):
        await f.create(new(f))

    assert (f.database.quotes, f.database.quote_numbers) == ({}, {})
    assert (await f.create()).number == "NF-2026-000001"


async def test_an_archived_customer_gets_no_new_quotes(f: Fixture) -> None:
    f.database.customers[f.acme.id].change(is_active=False)

    with pytest.raises(ArchivedCustomerError):
        await f.create()


async def test_get_quote_reads_the_tenants_quote(f: Fixture) -> None:
    quote = await f.create()
    reader = f.integration(Permission.QUOTES_READ)

    assert await get_quote(reader, quote.id, unit_of_work=f.unit_of_work) == quote


async def test_get_quote_of_another_tenant_is_not_found(f: Fixture) -> None:
    quote = await f.create()
    stranger = Principal(f.larkspur.id, uuid.uuid7(), Role.ADMIN)

    with pytest.raises(NotFoundError, match="no such quote"):
        await get_quote(stranger, quote.id, unit_of_work=f.unit_of_work)


async def test_get_quote_needs_quotes_read(f: Fixture) -> None:
    quote = await f.create()

    with pytest.raises(PermissionDeniedError, match="quotes:read"):
        await get_quote(
            f.integration(Permission.CATALOG_READ), quote.id, unit_of_work=f.unit_of_work
        )
