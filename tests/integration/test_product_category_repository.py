"""Product categories in PostgreSQL: Unicode order, keyset pages, unique names (ADR-0014)."""

import pytest

from pricewright.application.pagination import Keyset
from pricewright.domain.catalog import ProductCategory
from pricewright.domain.errors import ConflictError, StaleVersionError
from pricewright.domain.tenants import Tenant
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import Sessions, register

pytestmark = pytest.mark.integration


async def add(sessions: Sessions, tenant: Tenant, *names: str) -> list[ProductCategory]:
    categories = [ProductCategory.create(tenant_id=tenant.id, name=name) for name in names]
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(tenant.id)
        for category in categories:
            await uow.product_categories.add(category)
        await uow.commit()
    return categories


async def page(
    sessions: Sessions, tenant: Tenant, after: Keyset | None = None, limit: int = 10
) -> list[str]:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(tenant.id)
        categories = await uow.product_categories.page(after=after, limit=limit)
    return [category.name for category in categories]


async def test_categories_sort_as_people_read_them(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    await add(session_factory, northfield, "Zinc plating", "éclair tools", "Apple", "banana")

    names = await page(session_factory, northfield)

    # The server's default collation would give: Apple, Zinc plating, banana, éclair tools.
    assert names == ["Apple", "banana", "éclair tools", "Zinc plating"]


async def test_categories_continue_after_a_keyset(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    _, banana, _ = await add(session_factory, northfield, "apple", "Banana", "cherry")

    rest = await page(session_factory, northfield, after=Keyset(banana.id, banana.name))

    assert rest == ["cherry"]


async def test_category_names_are_unique_per_tenant_ignoring_case(
    session_factory: Sessions,
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    larkspur, _ = await register(session_factory, "Larkspur")
    await add(session_factory, northfield, "Fasteners")
    await add(session_factory, larkspur, "Fasteners")

    with pytest.raises(ConflictError):
        await add(session_factory, northfield, "FASTENERS")

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        found = await uow.product_categories.named("fasteners")
    assert found is not None
    assert found.name == "Fasteners"


async def test_a_category_is_renamed_with_compare_and_set(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    [category] = await add(session_factory, northfield, "Fastners")
    stale = ProductCategory(category.id, northfield.id, "Bolts", version=1)

    category.rename("Fasteners")
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        await uow.product_categories.save(category)
        await uow.commit()
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        with pytest.raises(StaleVersionError):
            await uow.product_categories.save(stale)
        stored = await uow.product_categories.get(category.id)

    assert stored == ProductCategory(category.id, northfield.id, "Fasteners", version=2)


async def test_another_tenants_category_is_invisible(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    larkspur, _ = await register(session_factory, "Larkspur")
    [category] = await add(session_factory, northfield, "Fasteners")

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(larkspur.id)
        hidden = await uow.product_categories.get(category.id)
        nameless = await uow.product_categories.named("Fasteners")
        with pytest.raises(RuntimeError):
            await uow.product_categories.save(category)
        with pytest.raises(RuntimeError):
            await uow.product_categories.add(
                ProductCategory.create(tenant_id=northfield.id, name="Smuggled")
            )

    assert (hidden, nameless) == (None, None)
    assert await page(session_factory, larkspur) == []
