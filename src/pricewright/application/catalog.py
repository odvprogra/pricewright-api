"""The tenant's catalog: everyone reads it (``catalog:read``), admins change it."""

from uuid import UUID

from pricewright.application.audit import category_fields, record
from pricewright.application.pagination import Keyset, Page, page_of
from pricewright.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from pricewright.domain.audit import AuditAction, changed, created
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.catalog import CategoryNameTakenError, ProductCategory
from pricewright.domain.errors import NotFoundError, StaleVersionError


async def _ensure_name_is_free(uow: UnitOfWork, category: ProductCategory) -> None:
    """Checked first for a clear error; the unique index still settles a race."""
    holder = await uow.product_categories.named(category.name)
    if holder is not None and holder.id != category.id:
        raise CategoryNameTakenError(f"there is already a category named {holder.name}")


async def create_category(
    principal: Principal, *, name: str, unit_of_work: UnitOfWorkFactory, clock: Clock
) -> ProductCategory:
    principal.require(Permission.CATALOG_MANAGE)
    category = ProductCategory.create(tenant_id=principal.tenant_id, name=name)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        await _ensure_name_is_free(uow, category)
        await uow.product_categories.add(category)
        changes = created(category_fields(category))
        action = AuditAction.PRODUCT_CATEGORY_CREATED
        await record(uow, principal, action, category.id, changes, now=clock())
        await uow.commit()
    return category


async def list_categories(
    principal: Principal, *, after: Keyset | None, limit: int, unit_of_work: UnitOfWorkFactory
) -> Page[ProductCategory]:
    """By name, as people read it (ADR-0014)."""
    principal.require(Permission.CATALOG_READ)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        categories = await uow.product_categories.page(after=after, limit=limit + 1)
    return page_of(categories, limit, lambda category: Keyset(category.id, category.name))


async def get_category(
    principal: Principal, category_id: UUID, *, unit_of_work: UnitOfWorkFactory
) -> ProductCategory:
    principal.require(Permission.CATALOG_READ)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        category = await uow.product_categories.get(category_id)
    if category is None:
        raise NotFoundError("no such category")
    return category


async def rename_category(
    principal: Principal,
    category_id: UUID,
    *,
    name: str,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> ProductCategory:
    principal.require(Permission.CATALOG_MANAGE)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        category = await uow.product_categories.get(category_id)
        if category is None:
            raise NotFoundError("no such category")
        if category.version != expected_version:
            raise StaleVersionError("the category was changed by someone else; reload it")
        before = category_fields(category)
        category.rename(name)
        await _ensure_name_is_free(uow, category)
        await uow.product_categories.save(category)
        changes = changed(before, category_fields(category))
        action = AuditAction.PRODUCT_CATEGORY_UPDATED
        await record(uow, principal, action, category.id, changes, now=clock())
        await uow.commit()
    return category
