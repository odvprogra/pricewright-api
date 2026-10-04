"""Price previews: what a quote would cost, line by line and why, without saving anything.

The same engine prices quotes (M4); a preview lets reps, managers and integrations (an LLM through
``erp-mcp-server``) see prices before a quote exists.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pricewright.application.ports import Clock, UnitOfWorkFactory
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.catalog import Product, UnknownProductError
from pricewright.domain.customers import UnknownCustomerError
from pricewright.domain.errors import NotFoundError
from pricewright.domain.pricing import LineRequest, PricedQuote, price_quote

MAX_PREVIEW_LINES = 100


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
        tenant = await uow.tenants.get(principal.tenant_id)
        if tenant is None:
            raise NotFoundError("the tenant no longer exists")
        # Another tenant's customer or product does not exist here (ADR-0009).
        customer = await uow.customers.get(customer_id)
        if customer is None:
            raise UnknownCustomerError("no such customer in this tenant")
        wanted = {line.product_id for line in lines}
        products = {product.id: product for product in await uow.products.with_ids(wanted)}
        if missing := wanted - products.keys():
            raise UnknownProductError(f"no such product in this tenant: {min(missing)}")
        rules = await uow.pricing_rules.effective_at(at)
    quote = price_quote(
        customer,
        [LineRequest(products[line.product_id], line.quantity) for line in lines],
        settings=tenant.settings,
        rules=rules,
        at=at,
    )
    return PricePreview(priced_at=at, quote=quote, products=products)
