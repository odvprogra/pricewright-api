"""Orders in PostgreSQL (ADR-0023): the snapshot round trip, a conversion saved with its quote,
compare-and-set cancellations, numbers in a series of their own, and the keys that keep one order
per quote."""

import asyncio
import dataclasses
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pricewright.application.ports import OrderQuery
from pricewright.domain.actors import Actor
from pricewright.domain.errors import StaleVersionError
from pricewright.domain.orders import Order, OrderStatus
from pricewright.domain.pricing import PriceOverride
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.quotes import Quote
from pricewright.domain.tenants import Tenant
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import Sessions
from tests.integration.stock import NOW, Stock, accepted, stock, usd

pytestmark = pytest.mark.integration


async def convert(
    sessions: Sessions, stock: Stock, quote: Quote, number: str = "ORD-2026-000001"
) -> Order:
    order = quote.convert(
        number=number,
        customer=stock.customer,
        by=Actor.person(stock.rep.id),
        now=NOW,
        reference="PO-4500123",
    )
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(stock.tenant.id)
        await uow.quotes.save(quote)  # the quote first: its version decides (ADR-0012)
        await uow.orders.add(order)
        await uow.commit()
    return order


async def get(sessions: Sessions, tenant: Tenant, order_id: uuid.UUID) -> Order | None:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(tenant.id)
        return await uow.orders.get(order_id)


async def test_order_repository_keeps_the_whole_snapshot(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)
    quote = await accepted(session_factory, northfield)

    order = await convert(session_factory, northfield, quote)

    stored = await get(session_factory, northfield.tenant, order.id)
    assert stored == order
    assert stored is not None
    assert [line.pricing for line in stored.lines] == [line.pricing for line in quote.lines]
    assert stored.lines[2].override == PriceOverride(usd("70"), "Agreed price")
    assert stored.lines[0].pricing.margin_floor is not None
    assert (stored.customer.account_number, stored.customer_reference) == ("C-1001", "PO-4500123")
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.tenant.id)
        converted = await uow.quotes.get(quote.id)
    assert converted is not None
    assert (converted.status, converted.order_id) == (QuoteStatus.CONVERTED, order.id)


async def test_order_repository_saves_a_cancellation_and_refuses_a_stale_one(
    session_factory: Sessions,
) -> None:
    northfield = await stock(session_factory)
    order = await convert(session_factory, northfield, await accepted(session_factory, northfield))
    stale = dataclasses.replace(order)
    order.cancel(reason="Entered twice", now=NOW)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.tenant.id)
        await uow.orders.save(order)
        await uow.commit()
    stale.cancel(reason="Also cancelled", now=NOW)

    assert await get(session_factory, northfield.tenant, order.id) == order
    assert (order.status, order.version) == (OrderStatus.CANCELLED, 2)
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.tenant.id)
        with pytest.raises(StaleVersionError):
            await uow.orders.save(stale)


async def test_concurrent_conversions_make_exactly_one_order(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)
    quote = await accepted(session_factory, northfield)
    both_have_read = asyncio.Barrier(2)

    async def convert_once(number: str) -> str:
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            uow.bind_tenant(northfield.tenant.id)
            current = await uow.quotes.get(quote.id)
            assert current is not None
            await both_have_read.wait()  # both convert the same version, whatever the scheduling
            order = current.convert(
                number=number,
                customer=northfield.customer,
                by=Actor.person(northfield.rep.id),
                now=NOW,
            )
            try:
                await uow.quotes.save(current)
            except StaleVersionError:
                return "stale"
            await uow.orders.add(order)
            await uow.commit()
            return "converted"

    results = await asyncio.gather(convert_once("ORD-2026-000001"), convert_once("ORD-2026-000002"))

    assert sorted(results) == ["converted", "stale"]
    async with session_factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM orders")) == 1


async def test_order_numbers_count_apart_from_quote_numbers(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)

    async def allocate(series: str, *, commit: bool = True) -> int:
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            uow.bind_tenant(northfield.tenant.id)
            repository = uow.quotes if series == "quote" else uow.orders
            number = await repository.allocate_number(2026)
            if commit:
                await uow.commit()
            return number

    quotes = [await allocate("quote"), await allocate("quote")]
    first_order = await allocate("order")
    taken_back = await allocate("order", commit=False)
    second_order = await allocate("order")

    assert quotes == [1, 2]
    assert (first_order, taken_back, second_order) == (1, 2, 2)  # a rollback leaves no gap


async def test_order_repository_only_sees_its_tenants_orders(session_factory: Sessions) -> None:
    northfield, larkspur = await stock(session_factory), await stock(session_factory, "Larkspur")
    order = await convert(session_factory, northfield, await accepted(session_factory, northfield))

    assert await get(session_factory, larkspur.tenant, order.id) is None
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(larkspur.tenant.id)
        with pytest.raises(RuntimeError, match="unit of work's tenant"):
            await uow.orders.add(order)
        with pytest.raises(RuntimeError, match="unit of work's tenant"):
            await uow.orders.save(order)


def another_order_of(order: Order) -> Order:
    return dataclasses.replace(
        order,
        id=uuid.uuid7(),
        number="ORD-2026-000002",
        lines=[dataclasses.replace(line, id=uuid.uuid7()) for line in order.lines],
    )


async def test_database_keeps_one_order_per_quote(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)
    order = await convert(session_factory, northfield, await accepted(session_factory, northfield))

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.tenant.id)
        with pytest.raises(IntegrityError, match="uq_orders_tenant_id_quote_id"):
            await uow.orders.add(another_order_of(order))


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("UPDATE quotes SET order_id = NULL", "ck_quotes_converted_links_its_order"),
        ("UPDATE orders SET total = total + 1", "ck_orders_total_adds_up"),
        ("UPDATE orders SET status = 'cancelled'", "ck_orders_cancelled_with_a_reason"),
    ],
)
async def test_database_refuses_orders_the_domain_forbids(
    session_factory: Sessions, statement: str, constraint: str
) -> None:
    northfield = await stock(session_factory)
    await convert(session_factory, northfield, await accepted(session_factory, northfield))

    async with session_factory() as session:
        with pytest.raises(IntegrityError, match=constraint):
            await session.execute(text(statement))  # statements are fixed in this test


async def test_database_refuses_a_quote_linked_to_a_missing_order_at_commit(
    session_factory: Sessions,
) -> None:
    northfield = await stock(session_factory)
    quote = await accepted(session_factory, northfield)
    order = quote.convert(
        number="ORD-2026-000001",
        customer=northfield.customer,
        by=Actor.person(northfield.rep.id),
        now=NOW,
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.tenant.id)
        await uow.quotes.save(quote)  # deferred: the order would be added next
        with pytest.raises(IntegrityError, match="fk_quotes_tenant_id_order_id_orders"):
            await uow.commit()
    assert order.quote_id == quote.id


async def test_order_list_filters_and_pages_by_creation(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)
    first = await convert(session_factory, northfield, await accepted(session_factory, northfield))
    second = await convert(
        session_factory,
        northfield,
        await accepted(session_factory, northfield, "NF-2026-000002"),
        "ORD-2026-000002",
    )
    second.cancel(reason="Entered twice", now=NOW)
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.tenant.id)
        await uow.orders.save(second)
        await uow.commit()

    async def page(query: OrderQuery, after: uuid.UUID | None = None) -> list[str]:
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            uow.bind_tenant(northfield.tenant.id)
            return [order.number for order in await uow.orders.page(query, after=after, limit=10)]

    assert await page(OrderQuery()) == ["ORD-2026-000002", "ORD-2026-000001"]
    assert await page(OrderQuery(descending=False)) == ["ORD-2026-000001", "ORD-2026-000002"]
    assert await page(OrderQuery(status=OrderStatus.OPEN)) == ["ORD-2026-000001"]
    assert await page(OrderQuery(number="ORD-2026-000002")) == ["ORD-2026-000002"]
    assert await page(OrderQuery(customer_id=northfield.customer.id, created_by=northfield.rep.id))
    assert await page(OrderQuery(created_by=northfield.manager.id)) == []
    assert await page(OrderQuery(), after=second.id) == ["ORD-2026-000001"]
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.tenant.id)
        [summary] = await uow.orders.page(OrderQuery(number="ORD-2026-000001"), after=None, limit=1)
    assert (summary.id, summary.quote_id, summary.total) == (
        first.id,
        first.quote_id,
        first.totals.total,
    )
    assert (summary.customer_name, summary.customer_reference) == ("Acme", "PO-4500123")
