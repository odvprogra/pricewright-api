"""Price previews: what a quote would cost, line by line and why, without saving anything.

The same engine prices quotes (ADR-0019); a preview lets reps, managers and integrations (an LLM
through ``erp-mcp-server``) see prices before a quote exists.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pricewright.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.catalog import Product, UnknownProductError
from pricewright.domain.customers import UnknownCustomerError
from pricewright.domain.errors import NotFoundError
from pricewright.domain.pricing import LineRequest, PricedQuote, price_quote
from pricewright.domain.quotes import PricingContext
from pricewright.domain.tenants import Tenant

MAX_PREVIEW_LINES = 100


async def tenant_of(uow: UnitOfWork, tenant_id: UUID) -> Tenant:
    tenant = await uow.tenants.get(tenant_id)
    if tenant is None:
        raise NotFoundError("the tenant no longer exists")
    return tenant


async def pricing_context(
    uow: UnitOfWork,
    tenant: Tenant,
    customer_id: UUID,
    product_ids: Collection[UUID],
    *,
    at: datetime,
) -> PricingContext:
    """What pricing reads, from the caller's tenant only: another tenant's customer or product does
    not exist here (ADR-0009)."""
    customer = await uow.customers.get(customer_id)
    if customer is None:
        raise UnknownCustomerError("no such customer in this tenant")
    wanted = set(product_ids)
    products = {product.id: product for product in await uow.products.with_ids(wanted)}
    if missing := wanted - products.keys():
        raise UnknownProductError(f"no such product in this tenant: {min(missing)}")
    return PricingContext(
        customer=customer,
        settings=tenant.settings,
        rules=await uow.pricing_rules.effective_at(at),
        products=products,
        at=at,
    )


@dataclass(frozen=True, slots=True)
class PreviewLine:
    product_id: UUID
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class PricePreview:
    priced_at: datetime
    quote: PricedQuote
    products: Mapping[UUID, Product]
    """The lines' products, for what a response shows besides prices (the SKU)."""


async def preview_prices(
    principal: Principal,
    customer_id: UUID,
    lines: Sequence[PreviewLine],
    *,
    priced_at: datetime | None,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> PricePreview:
    """Price ``lines`` for the customer with the rules effective ``priced_at`` (default: now)."""
    principal.require(Permission.PRICING_READ)
    at = clock() if priced_at is None else priced_at
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        tenant = await tenant_of(uow, principal.tenant_id)
        context = await pricing_context(
            uow, tenant, customer_id, {line.product_id for line in lines}, at=at
        )
    quote = price_quote(
        context.customer,
        [LineRequest(context.product(line.product_id), line.quantity) for line in lines],
        settings=context.settings,
        rules=context.rules,
        at=at,
    )
    return PricePreview(priced_at=at, quote=quote, products=context.products)
