"""Creating and reading quotes through the use cases, with in-memory fakes."""

import uuid
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

import pytest

from pricewright.application.pagination import Keyset
from pricewright.application.ports import QuoteQuery, QuoteSort, UnitOfWork
from pricewright.application.quotes import (
    NewQuote,
    QuoteTerms,
    change_quote_terms,
    create_quote,
    get_quote,
    list_quotes,
)
from pricewright.domain.actors import Actor
from pricewright.domain.audit import AuditAction
from pricewright.domain.auth import Permission, PermissionDeniedError, Principal
from pricewright.domain.catalog import Product, UnitOfMeasure, UnknownProductError
from pricewright.domain.customers import Customer, CustomerTier, UnknownCustomerError
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.money import Money
from pricewright.domain.pricing import ArchivedCustomerError, Stage
from pricewright.domain.pricing_rules import Bracket, PricingRule, RuleKind
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.quotes import (
    InvalidQuoteError,
    LineChange,
    Quote,
    QuoteNotEditableError,
)
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


async def test_list_quotes_shows_the_newest_first_across_pages(f: Fixture) -> None:
    created = [await f.create() for _ in range(3)]

    first = await list_quotes(f.rep, QuoteQuery(), after=None, limit=2, unit_of_work=f.unit_of_work)
    second = await list_quotes(
        f.rep, QuoteQuery(), after=first.next_after, limit=2, unit_of_work=f.unit_of_work
    )

    assert [q.number for q in first.items] == ["NF-2026-000003", "NF-2026-000002"]
    assert [q.id for q in second.items] == [created[0].id]
    assert second.next_after is None


async def test_list_quotes_sorts_by_validity_with_its_date_in_the_cursor(f: Fixture) -> None:
    late = await f.create(NewQuote(f.acme.id, valid_until=date(2026, 12, 31)))
    soon = await f.create(NewQuote(f.acme.id, valid_until=date(2026, 10, 10)))
    query = QuoteQuery(sort=QuoteSort.VALID_UNTIL, descending=False)

    page = await list_quotes(f.rep, query, after=None, limit=1, unit_of_work=f.unit_of_work)

    assert [q.id for q in page.items] == [soon.id]
    assert page.next_after == Keyset(soon.id, "2026-10-10")
    rest = await list_quotes(
        f.rep, query, after=page.next_after, limit=5, unit_of_work=f.unit_of_work
    )
    assert [q.id for q in rest.items] == [late.id]


async def test_list_quotes_filters_by_status_customer_number_and_creator(f: Fixture) -> None:
    mine = await f.create()
    integration = f.integration(Permission.QUOTES_MANAGE)
    theirs = await f.create(caller=integration)
    f.database.quotes[theirs.id].status = QuoteStatus.CANCELLED

    async def ids(query: QuoteQuery) -> list[uuid.UUID]:
        page = await list_quotes(f.rep, query, after=None, limit=10, unit_of_work=f.unit_of_work)
        return [quote.id for quote in page.items]

    assert await ids(QuoteQuery(status=QuoteStatus.DRAFT)) == [mine.id]
    assert await ids(QuoteQuery(created_by=integration.subject_id)) == [theirs.id]
    assert await ids(QuoteQuery(number="NF-2026-000001")) == [mine.id]
    assert await ids(QuoteQuery(customer_id=uuid.uuid7())) == []


async def test_list_quotes_never_shows_another_tenant(f: Fixture) -> None:
    await f.create()
    stranger = Principal(f.larkspur.id, uuid.uuid7(), Role.ADMIN)

    page = await list_quotes(
        stranger, QuoteQuery(), after=None, limit=10, unit_of_work=f.unit_of_work
    )

    assert page.items == []


async def test_list_quotes_needs_quotes_read(f: Fixture) -> None:
    with pytest.raises(PermissionDeniedError, match="quotes:read"):
        await list_quotes(
            f.integration(Permission.CATALOG_READ),
            QuoteQuery(),
            after=None,
            limit=10,
            unit_of_work=f.unit_of_work,
        )


async def change_terms(f: Fixture, quote: Quote, terms: QuoteTerms, *, version: int = 1) -> Quote:
    return await change_quote_terms(
        f.rep,
        quote.id,
        terms,
        expected_version=version,
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )


async def test_change_quote_terms_saves_a_new_version_and_records_it(f: Fixture) -> None:
    quote = await f.create(NewQuote(f.acme.id, notes="Rush order"))

    changed = await change_terms(f, quote, QuoteTerms(valid_until=date(2026, 12, 1), notes=None))

    assert (changed.valid_until, changed.notes, changed.version) == (date(2026, 12, 1), None, 2)
    assert f.database.quotes[quote.id].version == 2
    updated = [e for e in f.database.audit_events.values() if e.action is AuditAction.QUOTE_UPDATED]
    assert [event.changes for event in updated] == [
        {"valid_until": ("2026-11-02", "2026-12-01"), "notes": ("Rush order", None)}
    ]


async def test_change_quote_terms_keeps_the_notes_unless_asked(f: Fixture) -> None:
    quote = await f.create(NewQuote(f.acme.id, notes="Rush order"))

    changed = await change_terms(f, quote, QuoteTerms(valid_until=date(2026, 12, 1)))

    assert changed.notes == "Rush order"


async def test_change_quote_terms_based_on_an_old_version_is_rejected(f: Fixture) -> None:
    quote = await f.create()
    await change_terms(f, quote, QuoteTerms(notes="First"))

    with pytest.raises(StaleVersionError):
        await change_terms(f, quote, QuoteTerms(notes="Second"))

    assert f.database.quotes[quote.id].notes == "First"


async def test_change_quote_terms_of_a_submitted_quote_is_refused(f: Fixture) -> None:
    quote = await f.create()
    f.database.quotes[quote.id].status = QuoteStatus.APPROVED

    with pytest.raises(QuoteNotEditableError):
        await change_terms(f, quote, QuoteTerms(notes="Too late"))


async def test_change_quote_terms_of_another_tenant_is_not_found(f: Fixture) -> None:
    quote = await f.create()
    stranger = Principal(f.larkspur.id, uuid.uuid7(), Role.ADMIN)

    with pytest.raises(NotFoundError):
        await change_quote_terms(
            stranger,
            quote.id,
            QuoteTerms(notes="Hijacked"),
            expected_version=1,
            unit_of_work=f.unit_of_work,
            clock=f.clock,
        )


async def test_change_quote_terms_needs_quotes_manage(f: Fixture) -> None:
    quote = await f.create()

    with pytest.raises(PermissionDeniedError, match="quotes:manage"):
        await change_quote_terms(
            f.integration(Permission.QUOTES_READ),
            quote.id,
            QuoteTerms(notes="Read-only"),
            expected_version=1,
            unit_of_work=f.unit_of_work,
            clock=f.clock,
        )
