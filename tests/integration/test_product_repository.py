"""Products in PostgreSQL: filters, sorts and keyset pages (ADR-0014), and the keys that keep a
product inside its tenant and its tenant's currency (ADR-0003, ADR-0006)."""

import dataclasses
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from pricewright.application.pagination import Keyset
from pricewright.application.ports import ProductQuery, ProductSort
from pricewright.domain.catalog import Product, ProductCategory, UnitOfMeasure
from pricewright.domain.errors import ConflictError, StaleVersionError
from pricewright.domain.money import Money
from pricewright.domain.tenants import Tenant
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import Sessions, register

pytestmark = pytest.mark.integration


def product(
    tenant: Tenant,
    sku: str,
    name: str,
    *,
    category_id: uuid.UUID | None = None,
    is_active: bool = True,
    price: Money | None = None,
) -> Product:
    created = Product.create(
        tenant_id=tenant.id,
        currency="USD",
        sku=sku,
        name=name,
        unit=UnitOfMeasure.EACH,
        list_price=Money(Decimal("12.3456"), "USD"),
        unit_cost=Money(Decimal("7.5"), "USD"),
        category_id=category_id,
    )
    created.is_active = is_active
    if price is not None:  # bypasses the domain's currency check, to reach the database's
        created.list_price = created.unit_cost = price
    return created


async def store(sessions: Sessions, tenant: Tenant, *items: Product | ProductCategory) -> None:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(tenant.id)
        for item in items:
            if isinstance(item, Product):
                await uow.products.add(item)
            else:
                await uow.product_categories.add(item)
        await uow.commit()


async def skus(
    sessions: Sessions, tenant: Tenant, query: ProductQuery, after: Keyset | None = None
) -> list[str]:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(tenant.id)
        products = await uow.products.page(query, after=after, limit=10)
    return [found.sku for found in products]


async def test_products_round_trip_their_prices_exactly(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    tape = product(northfield, "TAPE-48", "Packing tape")
    await store(session_factory, northfield, tape)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        found = await uow.products.get(tape.id)
        by_sku = await uow.products.with_sku("tape-48")

    assert found == by_sku == tape
    assert found is not None
    assert str(found.list_price.amount) == "12.3456"


@pytest.mark.parametrize(
    ("sort", "descending", "expected"),
    [
        (ProductSort.NAME, False, ["B-2", "a-1", "C-3"]),
        (ProductSort.NAME, True, ["C-3", "a-1", "B-2"]),
        (ProductSort.SKU, False, ["a-1", "B-2", "C-3"]),
        (ProductSort.SKU, True, ["C-3", "B-2", "a-1"]),
        (ProductSort.CREATED, False, ["C-3", "a-1", "B-2"]),
        (ProductSort.CREATED, True, ["B-2", "a-1", "C-3"]),
    ],
)
async def test_products_sort_by_name_sku_or_creation(
    session_factory: Sessions, sort: ProductSort, descending: bool, expected: list[str]
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    await store(
        session_factory,
        northfield,
        product(northfield, "C-3", "zipper bags"),
        product(northfield, "a-1", "Bubble wrap"),
        product(northfield, "B-2", "Adhesive labels"),
    )

    found = await skus(session_factory, northfield, ProductQuery(sort=sort, descending=descending))

    assert found == expected


@pytest.mark.parametrize(
    ("sort", "descending", "value"),
    [(ProductSort.NAME, False, "Adhesive labels"), (ProductSort.CREATED, True, None)],
)
async def test_products_continue_after_a_keyset(
    session_factory: Sessions, sort: ProductSort, descending: bool, value: str | None
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    wrap = product(northfield, "A-1", "Bubble wrap")  # created in this order
    labels = product(northfield, "B-2", "Adhesive labels")
    ties = product(northfield, "C-3", "Cable ties")
    await store(session_factory, northfield, wrap, labels, ties)
    query = ProductQuery(sort=sort, descending=descending)

    rest = await skus(session_factory, northfield, query, after=Keyset(labels.id, value))

    assert rest == (["A-1", "C-3"] if sort is ProductSort.NAME else ["A-1"])


async def test_products_are_filtered(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    fasteners = ProductCategory.create(tenant_id=northfield.id, name="Fasteners")
    await store(
        session_factory,
        northfield,
        fasteners,
        product(northfield, "BOLT-M6", "Hex bolt 100% steel", category_id=fasteners.id),
        product(northfield, "NUT_M6", "Hex nut", category_id=fasteners.id, is_active=False),
        product(northfield, "TAPE-48", "Packing tape"),
    )

    async def matching(query: ProductQuery) -> list[str]:
        return await skus(session_factory, northfield, query)

    by_sku = ProductSort.SKU
    assert await matching(ProductQuery(text="HEX", sort=by_sku)) == ["BOLT-M6", "NUT_M6"]
    assert await matching(ProductQuery(text="m6", sort=by_sku)) == ["BOLT-M6", "NUT_M6"]
    assert await matching(ProductQuery(text="100%")) == ["BOLT-M6"]  # a literal percent sign
    assert await matching(ProductQuery(text="T_M")) == ["NUT_M6"]  # a literal underscore
    assert await matching(ProductQuery(sku="tape-48")) == ["TAPE-48"]
    assert await matching(ProductQuery(category_id=fasteners.id, sort=by_sku)) == [
        "BOLT-M6",
        "NUT_M6",
    ]
    assert await matching(ProductQuery(active=True, sort=by_sku)) == ["BOLT-M6", "TAPE-48"]
    assert await matching(ProductQuery(text="hex", active=False)) == ["NUT_M6"]


async def test_skus_are_unique_per_tenant_ignoring_case(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    larkspur, _ = await register(session_factory, "Larkspur")
    await store(session_factory, northfield, product(northfield, "TAPE-48", "Packing tape"))
    await store(session_factory, larkspur, product(larkspur, "TAPE-48", "Packing tape"))

    with pytest.raises(ConflictError):
        await store(session_factory, northfield, product(northfield, "tape-48", "Other tape"))


async def test_prices_must_be_in_the_tenants_currency(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    smuggled = product(northfield, "EUR-1", "Priced in euros", price=Money(Decimal(1), "EUR"))

    with pytest.raises(IntegrityError, match="fk_products_tenant_id_currency_tenants"):
        await store(session_factory, northfield, smuggled)


async def test_a_product_cannot_use_another_tenants_category(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    larkspur, _ = await register(session_factory, "Larkspur")
    theirs = ProductCategory.create(tenant_id=larkspur.id, name="Fasteners")
    await store(session_factory, larkspur, theirs)

    with pytest.raises(IntegrityError, match="fk_products_tenant_id_category_id"):
        await store(
            session_factory,
            northfield,
            product(northfield, "BOLT-M6", "Hex bolt", category_id=theirs.id),
        )


async def test_a_product_is_saved_with_compare_and_set(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    tape = product(northfield, "TAPE-48", "Packing tape")
    await store(session_factory, northfield, tape)
    stale = dataclasses.replace(tape)

    tape.change(list_price=Money(Decimal("13"), "USD"), is_active=False)
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        await uow.products.save(tape)
        await uow.commit()
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        with pytest.raises(StaleVersionError):
            await uow.products.save(stale)
        stored = await uow.products.get(tape.id)

    assert stored == tape
    assert tape.version == 2


async def test_another_tenants_products_are_invisible(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    larkspur, _ = await register(session_factory, "Larkspur")
    tape = product(northfield, "TAPE-48", "Packing tape")
    await store(session_factory, northfield, tape)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(larkspur.id)
        hidden = await uow.products.get(tape.id)
        by_sku = await uow.products.with_sku("TAPE-48")
        with pytest.raises(RuntimeError):
            await uow.products.save(tape)
        with pytest.raises(RuntimeError):
            await uow.products.add(tape)

    assert (hidden, by_sku) == (None, None)
    assert await skus(session_factory, larkspur, ProductQuery()) == []
