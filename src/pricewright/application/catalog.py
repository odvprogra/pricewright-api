"""The tenant's catalog: everyone reads it (``catalog:read``), admins change it."""

from dataclasses import dataclass
from uuid import UUID

from pricewright.application.audit import category_fields, product_fields, record
from pricewright.application.idempotency import (
    Created,
    earlier_creation,
    remember_creation,
    replay,
)
from pricewright.application.pagination import Keyset, Page, page_of
from pricewright.application.ports import (
    Clock,
    ProductQuery,
    ProductSort,
    UnitOfWork,
    UnitOfWorkFactory,
)
from pricewright.domain.audit import AuditAction, AuditResourceType, changed, created
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.catalog import (
    CategoryNameTakenError,
    Product,
    ProductCategory,
    SkuTakenError,
    UnitOfMeasure,
    UnknownCategoryError,
    UnknownProductError,
)
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.idempotency import IdempotentRequest
from pricewright.domain.money import Money
from pricewright.domain.updates import KEEP, Keep


async def _ensure_name_is_free(uow: UnitOfWork, category: ProductCategory) -> None:
    """Checked first for a clear error; the unique index still settles a race."""
    holder = await uow.product_categories.named(category.name)
    if holder is not None and holder.id != category.id:
        raise CategoryNameTakenError(f"there is already a category named {holder.name}")


async def create_category(
    principal: Principal,
    *,
    name: str,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
    idempotency: IdempotentRequest | None = None,
) -> Created[ProductCategory]:
    """A retry with the same ``Idempotency-Key`` gets the category back (ADR-0022)."""
    principal.require(Permission.CATALOG_MANAGE)
    now = clock()
    category = ProductCategory.create(tenant_id=principal.tenant_id, name=name)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        if (earlier := await earlier_creation(uow, principal, idempotency, now=now)) is not None:
            return await replay(uow.product_categories.get(earlier))
        await _ensure_name_is_free(uow, category)
        await uow.product_categories.add(category)
        changes = created(category_fields(category))
        action = AuditAction.PRODUCT_CATEGORY_CREATED
        await record(uow, principal, action, category.id, changes, now=now)
        await remember_creation(
            uow, principal, idempotency, AuditResourceType.PRODUCT_CATEGORY, category.id, now=now
        )
        await uow.commit()
    return Created(category)


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


async def ensure_category_exists(uow: UnitOfWork, category_id: UUID | None) -> None:
    """Another tenant's category does not exist here (ADR-0009)."""
    if category_id is not None and await uow.product_categories.get(category_id) is None:
        raise UnknownCategoryError("no such category in this tenant")


async def ensure_product_exists(uow: UnitOfWork, product_id: UUID | None) -> None:
    """Another tenant's product does not exist here (ADR-0009)."""
    if product_id is not None and await uow.products.get(product_id) is None:
        raise UnknownProductError("no such product in this tenant")


async def _tenant_currency(uow: UnitOfWork, tenant_id: UUID) -> str:
    tenant = await uow.tenants.get(tenant_id)
    if tenant is None:
        raise NotFoundError("the tenant no longer exists")
    return tenant.settings.currency


@dataclass(frozen=True, slots=True)
class NewProduct:
    sku: str
    name: str
    unit: UnitOfMeasure
    list_price: Money
    unit_cost: Money
    category_id: UUID | None = None


async def create_product(
    principal: Principal,
    new: NewProduct,
    *,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
    idempotency: IdempotentRequest | None = None,
) -> Created[Product]:
    """A retry with the same ``Idempotency-Key`` gets the product back (ADR-0022)."""
    principal.require(Permission.CATALOG_MANAGE)
    now = clock()
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        if (earlier := await earlier_creation(uow, principal, idempotency, now=now)) is not None:
            return await replay(uow.products.get(earlier))
        product = Product.create(
            tenant_id=principal.tenant_id,
            currency=await _tenant_currency(uow, principal.tenant_id),
            sku=new.sku,
            name=new.name,
            unit=new.unit,
            list_price=new.list_price,
            unit_cost=new.unit_cost,
            category_id=new.category_id,
        )
        await ensure_category_exists(uow, product.category_id)
        if (holder := await uow.products.with_sku(product.sku)) is not None:
            raise SkuTakenError(f"SKU {holder.sku} is already in the catalog")
        await uow.products.add(product)
        changes = created(product_fields(product))
        await record(uow, principal, AuditAction.PRODUCT_CREATED, product.id, changes, now=now)
        await remember_creation(
            uow, principal, idempotency, AuditResourceType.PRODUCT, product.id, now=now
        )
        await uow.commit()  # the unique index still settles a race on the SKU
    return Created(product)


def _position(product: Product, sort: ProductSort) -> Keyset:
    values = {ProductSort.SKU: product.sku, ProductSort.NAME: product.name}
    return Keyset(product.id, values.get(sort))


async def list_products(
    principal: Principal,
    query: ProductQuery,
    *,
    after: Keyset | None,
    limit: int,
    unit_of_work: UnitOfWorkFactory,
) -> Page[Product]:
    principal.require(Permission.CATALOG_READ)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        products = await uow.products.page(query, after=after, limit=limit + 1)
    return page_of(products, limit, lambda product: _position(product, query.sort))


async def get_product(
    principal: Principal, product_id: UUID, *, unit_of_work: UnitOfWorkFactory
) -> Product:
    principal.require(Permission.CATALOG_READ)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        product = await uow.products.get(product_id)
    if product is None:
        raise NotFoundError("no such product")
    return product


@dataclass(frozen=True, slots=True)
class ProductChanges:
    """Fields left as ``None`` keep their value; the category keeps it with ``KEEP``."""

    name: str | None = None
    unit: UnitOfMeasure | None = None
    list_price: Money | None = None
    unit_cost: Money | None = None
    category_id: UUID | Keep | None = KEEP
    is_active: bool | None = None


async def change_product(
    principal: Principal,
    product_id: UUID,
    changes: ProductChanges,
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> Product:
    """Edit, archive (``is_active=False``) or restore a product. Its SKU never changes."""
    principal.require(Permission.CATALOG_MANAGE)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        product = await uow.products.get(product_id)
        if product is None:
            raise NotFoundError("no such product")
        if product.version != expected_version:
            raise StaleVersionError("the product was changed by someone else; reload it")
        if changes.category_id is not KEEP:
            await ensure_category_exists(uow, changes.category_id)
        before = product_fields(product)
        product.change(
            name=changes.name,
            unit=changes.unit,
            list_price=changes.list_price,
            unit_cost=changes.unit_cost,
            category_id=changes.category_id,
            is_active=changes.is_active,
        )
        await uow.products.save(product)
        edits = changed(before, product_fields(product))
        await record(uow, principal, AuditAction.PRODUCT_UPDATED, product.id, edits, now=clock())
        await uow.commit()
    return product
