"""Orders: what customers committed to, converted once from accepted quotes (ADR-0023).

Converting is a quote transition (``application.quotes.convert_quote``); here orders are read and
cancelled.
"""

from uuid import UUID

from pricewright.application.audit import order_fields, record
from pricewright.application.pagination import Keyset, Page, page_of
from pricewright.application.ports import (
    Clock,
    OrderQuery,
    OrderSummary,
    UnitOfWork,
    UnitOfWorkFactory,
)
from pricewright.domain.audit import AuditAction, changed
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.orders import Order


async def order_of(uow: UnitOfWork, order_id: UUID) -> Order:
    order = await uow.orders.get(order_id)
    if order is None:
        raise NotFoundError("no such order")
    return order


async def get_order(
    principal: Principal, order_id: UUID, *, unit_of_work: UnitOfWorkFactory
) -> Order:
    principal.require(Permission.ORDERS_READ)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        return await order_of(uow, order_id)


async def list_orders(
    principal: Principal,
    query: OrderQuery,
    *,
    after: Keyset | None,
    limit: int,
    unit_of_work: UnitOfWorkFactory,
) -> Page[OrderSummary]:
    principal.require(Permission.ORDERS_READ)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        orders = await uow.orders.page(
            query, after=None if after is None else after.id, limit=limit + 1
        )
    return page_of(orders, limit, lambda order: Keyset(order.id))


async def cancel_order(
    principal: Principal,
    order_id: UUID,
    reason: str,
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> Order:
    """Withdraw an open order, with a reason (people only: ``orders:manage``). The order stays,
    and its quote stays converted (ADR-0023)."""
    principal.require(Permission.ORDERS_MANAGE)
    now = clock()
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        order = await order_of(uow, order_id)
        if order.version != expected_version:
            raise StaleVersionError("the order was changed by someone else; reload it")
        before = order_fields(order)
        order.cancel(reason=reason, now=now)
        await uow.orders.save(order)
        cancelled = changed(before, order_fields(order))
        await record(uow, principal, AuditAction.ORDER_CANCELLED, order.id, cancelled, now=now)
        await uow.commit()
    return order
