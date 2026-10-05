"""Quotes in PostgreSQL: the snapshot round trip, compare-and-set saves, revisions, the number
counter, and the keys and checks that keep quotes consistent (ADR-0005, ADR-0019 to ADR-0021)."""

import asyncio
import dataclasses
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pricewright.application.pagination import Keyset
from pricewright.application.ports import QuoteQuery, QuoteSort
from pricewright.domain.actors import Actor
from pricewright.domain.catalog import Product, UnitOfMeasure
from pricewright.domain.customers import Customer, CustomerTier
from pricewright.domain.errors import StaleVersionError
from pricewright.domain.money import Money
from pricewright.domain.pricing import PriceOverride, RateOverride
from pricewright.domain.pricing_rules import Bracket, PricingRule, RuleKind
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.quotes import LineChange, PricingContext, Quote
from pricewright.domain.tenants import Tenant
from pricewright.domain.users import User
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import Sessions, register

pytestmark = pytest.mark.integration

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


async def add(sessions: Sessions, stock: Stock, quote: Quote) -> None:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(stock.tenant.id)
        await uow.quotes.add(quote)
        await uow.commit()


async def get(sessions: Sessions, stock: Stock, quote_id: uuid.UUID) -> Quote | None:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(stock.tenant.id)
        return await uow.quotes.get(quote_id)


async def save(sessions: Sessions, stock: Stock, quote: Quote) -> None:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(stock.tenant.id)
        await uow.quotes.save(quote)
        await uow.commit()


async def test_quote_repository_keeps_the_whole_snapshot(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)
    quote = draft(northfield)

    await add(session_factory, northfield, quote)

    stored = await get(session_factory, northfield, quote.id)
    assert stored == quote
    assert stored is not None
    steps = [step.stage for step in stored.lines[0].pricing.breakdown.steps]
    assert [step.value for step in steps] == ["volume_tier"]
    assert stored.lines[2].override == PriceOverride(usd("70"), "Agreed price")
    assert stored.lines[0].pricing.margin_floor is not None


async def test_quote_repository_saves_transitions_and_approvals(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)
    quote = draft(northfield)
    await add(session_factory, northfield, quote)
    quote.submit(by=Actor.person(northfield.rep.id), context=northfield.pricing(NOW))
    assert quote.status is QuoteStatus.PENDING_APPROVAL

    await save(session_factory, northfield, quote)
    pending = await get(session_factory, northfield, quote.id)

    assert pending == quote
    assert quote.version == 2
    quote.approve(by=Actor.person(northfield.director.id), comment="Strategic account", now=NOW)
    await save(session_factory, northfield, quote)

    approved = await get(session_factory, northfield, quote.id)
    assert approved == quote
    assert quote.approvals[0].decided_by == northfield.director.id


async def test_quote_repository_keeps_an_empty_draft(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)
    quote = Quote.draft(
        number="NF-2026-000001",
        valid_until=date(2026, 11, 14),
        by=Actor.person(northfield.rep.id),
        context=northfield.pricing(),
    )

    await add(session_factory, northfield, quote)

    assert await get(session_factory, northfield, quote.id) == quote


async def test_quote_repository_refuses_to_save_over_a_newer_version(
    session_factory: Sessions,
) -> None:
    northfield = await stock(session_factory)
    quote = draft(northfield)
    await add(session_factory, northfield, quote)
    quote.version = 0  # pretend it was read before an earlier save

    with pytest.raises(StaleVersionError):
        await save(session_factory, northfield, quote)


async def test_quote_repository_replaces_the_lines_in_their_order(
    session_factory: Sessions,
) -> None:
    northfield = await stock(session_factory)
    quote = draft(northfield)
    await add(session_factory, northfield, quote)
    quote.remove_line(quote.lines[0].id, context=northfield.pricing())

    await save(session_factory, northfield, quote)

    stored = await get(session_factory, northfield, quote.id)
    assert stored is not None
    assert [line.id for line in stored.lines] == [line.id for line in quote.lines]
    assert stored.totals == quote.totals


async def test_quote_repository_links_revisions_both_ways(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)
    quote = draft(northfield)
    quote.submit(by=Actor.person(northfield.rep.id), context=northfield.pricing())
    quote.recall(now=NOW)
    quote.cancel(reason="Customer declined", now=NOW)
    await add(session_factory, northfield, quote)
    sent = draft(northfield, "NF-2026-000002")
    sent.lines = sent.lines[:1]
    sent.reprice(northfield.pricing())
    sent.submit(by=Actor.person(northfield.rep.id), context=northfield.pricing())
    sent.send(now=NOW)
    await add(session_factory, northfield, sent)

    successor = sent.revise(by=Actor.person(northfield.rep.id), context=northfield.pricing())
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.tenant.id)
        await uow.quotes.save(sent)  # the predecessor first: the version decides (ADR-0012)
        await uow.quotes.add(successor)
        await uow.commit()

    old = await get(session_factory, northfield, sent.id)
    new = await get(session_factory, northfield, successor.id)
    assert old is not None
    assert new is not None
    assert (old.status, old.superseded_by_id) == (QuoteStatus.SUPERSEDED, successor.id)
    assert (new.supersedes_id, new.number, new.revision) == (sent.id, sent.number, 2)
    assert (await get(session_factory, northfield, quote.id)) == quote


async def test_concurrent_revisions_let_exactly_one_win(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)
    quote = draft(northfield)
    quote.lines = quote.lines[:1]
    quote.reprice(northfield.pricing())
    quote.submit(by=Actor.person(northfield.rep.id), context=northfield.pricing())
    quote.send(now=NOW)
    await add(session_factory, northfield, quote)
    both_have_read = asyncio.Barrier(2)

    async def revise() -> str:
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            uow.bind_tenant(northfield.tenant.id)
            current = await uow.quotes.get(quote.id)
            assert current is not None
            await both_have_read.wait()  # both revise the same version, whatever the scheduling
            successor = current.revise(
                by=Actor.person(northfield.rep.id), context=northfield.pricing()
            )
            try:
                await uow.quotes.save(current)
            except StaleVersionError:
                return "stale"
            await uow.quotes.add(successor)
            await uow.commit()
            return "revised"

    results = await asyncio.gather(revise(), revise())

    assert sorted(results) == ["revised", "stale"]


async def test_quote_repository_only_sees_its_tenants_quotes(session_factory: Sessions) -> None:
    northfield, larkspur = await stock(session_factory), await stock(session_factory, "Larkspur")
    quote = draft(northfield)
    await add(session_factory, northfield, quote)

    assert await get(session_factory, larkspur, quote.id) is None
    with pytest.raises(RuntimeError, match="unit of work's tenant"):
        await add(session_factory, larkspur, draft(northfield, "NF-2026-000002"))


async def test_quote_numbers_count_per_tenant_and_year(session_factory: Sessions) -> None:
    northfield, larkspur = await stock(session_factory), await stock(session_factory, "Larkspur")

    async def allocate(tenant: Tenant, year: int, *, commit: bool = True) -> int:
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            uow.bind_tenant(tenant.id)
            number = await uow.quotes.allocate_number(year)
            if commit:
                await uow.commit()
            return number

    first = await allocate(northfield.tenant, 2026)
    second = await allocate(northfield.tenant, 2026)
    taken_back = await allocate(northfield.tenant, 2026, commit=False)
    third = await allocate(northfield.tenant, 2026)

    assert (first, second, taken_back, third) == (1, 2, 3, 3)  # a rollback leaves no gap
    assert await allocate(northfield.tenant, 2027) == 1
    assert await allocate(larkspur.tenant, 2026) == 1


async def test_concurrent_allocations_get_different_numbers(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)

    async def allocate() -> int:
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            uow.bind_tenant(northfield.tenant.id)
            number = await uow.quotes.allocate_number(2026)
            await uow.commit()
            return number

    numbers = await asyncio.gather(*(allocate() for _ in range(5)))

    assert sorted(numbers) == [1, 2, 3, 4, 5]


async def test_database_refuses_a_superseded_quote_without_its_successor(
    session_factory: Sessions,
) -> None:
    northfield = await stock(session_factory)
    quote = draft(northfield)
    quote.status = QuoteStatus.SUPERSEDED

    with pytest.raises(IntegrityError, match="ck_quotes_superseded_links_forward"):
        await add(session_factory, northfield, quote)


async def test_database_checks_the_successor_link_at_commit(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)
    quote = draft(northfield)
    quote.status, quote.superseded_by_id = QuoteStatus.SUPERSEDED, uuid.uuid7()

    with pytest.raises(IntegrityError, match="fk_quotes_tenant_id_superseded_by_id_quotes"):
        await add(session_factory, northfield, quote)


async def test_database_keeps_one_pending_request_per_quote(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)
    quote = draft(northfield)
    quote.submit(by=Actor.person(northfield.rep.id), context=northfield.pricing())
    quote.approvals.append(dataclasses.replace(quote.approvals[0], id=uuid.uuid7()))

    with pytest.raises(IntegrityError, match="uq_approval_requests_one_pending_per_quote"):
        await add(session_factory, northfield, quote)


async def test_database_refuses_a_line_for_another_tenants_product(
    session_factory: Sessions,
) -> None:
    northfield, larkspur = await stock(session_factory), await stock(session_factory, "Larkspur")
    quote = draft(northfield)
    quote.lines[0] = dataclasses.replace(quote.lines[0], product_id=larkspur.bolts.id)

    with pytest.raises(IntegrityError, match="fk_quote_lines_tenant_id_product_id_products"):
        await add(session_factory, northfield, quote)


async def test_quote_lines_go_with_their_quote(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)
    quote = draft(northfield)
    await add(session_factory, northfield, quote)

    async with session_factory() as session:
        await session.execute(text("DELETE FROM quotes"))
        remaining = await session.scalar(text("SELECT count(*) FROM quote_lines"))
        await session.rollback()

    assert remaining == 0


async def page(
    sessions: Sessions, stock: Stock, query: QuoteQuery, after: Keyset | None = None
) -> list[uuid.UUID]:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(stock.tenant.id)
        found = await uow.quotes.page(query, after=after, limit=10)
    return [quote.id for quote in found]


async def test_quote_list_filters_and_sorts_with_keyset_pages(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)
    first, second, third = (draft(northfield, f"NF-2026-00000{n}") for n in (1, 2, 3))
    first.valid_until, second.valid_until, third.valid_until = (
        date(2026, 12, 31),
        date(2026, 10, 20),
        date(2026, 10, 20),
    )
    third.created_by = Actor.person(northfield.manager.id)
    second.cancel(reason="Customer declined", now=NOW)
    for quote in (first, second, third):
        await add(session_factory, northfield, quote)
    by_date = QuoteQuery(sort=QuoteSort.VALID_UNTIL, descending=False)

    assert await page(session_factory, northfield, QuoteQuery()) == [third.id, second.id, first.id]
    assert await page(session_factory, northfield, by_date) == [second.id, third.id, first.id]
    assert await page(
        session_factory, northfield, by_date, after=Keyset(second.id, "2026-10-20")
    ) == [third.id, first.id]
    assert await page(session_factory, northfield, QuoteQuery(status=QuoteStatus.CANCELLED)) == [
        second.id
    ]
    assert await page(
        session_factory, northfield, QuoteQuery(created_by=northfield.manager.id)
    ) == [third.id]
    assert await page(session_factory, northfield, QuoteQuery(number="NF-2026-000001")) == [
        first.id
    ]
    assert await page(
        session_factory, northfield, QuoteQuery(customer_id=northfield.customer.id)
    ) == [third.id, second.id, first.id]


async def test_quote_list_summaries_carry_the_totals(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)
    quote = draft(northfield)
    await add(session_factory, northfield, quote)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.tenant.id)
        [summary] = await uow.quotes.page(QuoteQuery(), after=None, limit=10)

    assert (summary.display_number, summary.status, summary.total) == (
        "NF-2026-000001",
        QuoteStatus.DRAFT,
        quote.totals.total,
    )
    assert (summary.created_by, summary.version) == (quote.created_by, 1)
