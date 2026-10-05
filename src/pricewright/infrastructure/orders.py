"""The SQLAlchemy order repository (ADR-0011, ADR-0023): an order and its lines.

Statements are explicit and in foreign-key order. An order's lines are written once, with the
order; a save only changes its status, compare-and-set on the version (ADR-0012).
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pricewright.domain.actors import Actor
from pricewright.domain.audit import ActorType
from pricewright.domain.catalog import UnitOfMeasure
from pricewright.domain.errors import StaleVersionError
from pricewright.domain.money import Money
from pricewright.domain.numbering import NumberSeries
from pricewright.domain.orders import (
    CustomerSnapshot,
    Order,
    OrderLine,
    OrderStatus,
    OrderTotals,
)
from pricewright.infrastructure.line_snapshots import override_of, priced_line, snapshot_values
from pricewright.infrastructure.numbering import allocate_number
from pricewright.infrastructure.records import OrderLineRecord, OrderRecord
from pricewright.infrastructure.repositories import TenantScope

# Column values for SQL statements: ``Any`` at this boundary, since each column has its own type.


def _status_values(order: Order) -> dict[str, Any]:
    """The columns a save may change."""
    return {
        "status": order.status.value,
        "status_changed_at": order.status_changed_at,
        "cancel_reason": order.cancel_reason,
    }


def _order_values(order: Order) -> dict[str, Any]:
    totals, customer = order.totals, order.customer
    return {
        "id": order.id,
        "tenant_id": order.tenant_id,
        "number": order.number,
        "quote_id": order.quote_id,
        "quote_number": order.quote_number,
        "customer_id": customer.id,
        "customer_account_number": customer.account_number,
        "customer_name": customer.name,
        "customer_tax_id": customer.tax_id,
        "customer_payment_terms_days": customer.payment_terms_days,
        "customer_reference": order.customer_reference,
        "currency": order.currency,
        "list_subtotal": totals.list_subtotal.amount,
        "net_subtotal": totals.net_subtotal.amount,
        "tax_rate": totals.tax_rate,
        "tax": totals.tax.amount,
        "total": totals.total.amount,
        "priced_at": totals.priced_at,
        "created_by_type": order.created_by.type.value,
        "created_by_id": order.created_by.id,
        "created_at": order.created_at,
        "version": order.version,
        **_status_values(order),
    }


def _line_values(order: Order, line: OrderLine, position: int) -> dict[str, Any]:
    return {
        "id": line.id,
        "tenant_id": order.tenant_id,
        "order_id": order.id,
        "position": position,
        **snapshot_values(line, order.currency),
    }


def _to_line(record: OrderLineRecord) -> OrderLine:
    return OrderLine(
        id=record.id,
        product_id=record.product_id,
        sku=record.sku,
        product_name=record.product_name,
        unit=UnitOfMeasure(record.unit),
        quantity=record.quantity,
        pricing=priced_line(record),
        override=override_of(record),
        override_by=record.override_by,
    )


def _to_order(record: OrderRecord, lines: Sequence[OrderLineRecord]) -> Order:
    currency = record.currency
    return Order(
        id=record.id,
        tenant_id=record.tenant_id,
        number=record.number,
        quote_id=record.quote_id,
        quote_number=record.quote_number,
        customer=CustomerSnapshot(
            id=record.customer_id,
            account_number=record.customer_account_number,
            name=record.customer_name,
            tax_id=record.customer_tax_id,
            payment_terms_days=record.customer_payment_terms_days,
        ),
        currency=currency,
        totals=OrderTotals(
            list_subtotal=Money(record.list_subtotal, currency),
            net_subtotal=Money(record.net_subtotal, currency),
            tax_rate=record.tax_rate,
            tax=Money(record.tax, currency),
            total=Money(record.total, currency),
            priced_at=record.priced_at,
        ),
        created_by=Actor(ActorType(record.created_by_type), record.created_by_id),
        created_at=record.created_at,
        lines=[_to_line(line) for line in lines],
        customer_reference=record.customer_reference,
        status=OrderStatus(record.status),
        status_changed_at=record.status_changed_at,
        cancel_reason=record.cancel_reason,
        version=record.version,
    )


class SqlAlchemyOrderRepository:
    def __init__(self, session: AsyncSession, scope: TenantScope) -> None:
        self._session = session
        self._scope = scope

    def _require_own(self, order: Order) -> None:
        if order.tenant_id != self._scope.tenant_id:
            raise RuntimeError("only an order of the unit of work's tenant can be stored")

    async def allocate_number(self, year: int) -> int:
        return await allocate_number(self._session, self._scope.tenant_id, NumberSeries.ORDER, year)

    async def add(self, order: Order) -> None:
        self._require_own(order)
        await self._session.execute(insert(OrderRecord).values(**_order_values(order)))
        # Never empty: an order comes from an accepted quote, and an empty quote cannot be sent.
        await self._session.execute(
            insert(OrderLineRecord),
            [_line_values(order, line, position) for position, line in enumerate(order.lines)],
        )

    async def get(self, order_id: UUID) -> Order | None:
        tenant_id = self._scope.tenant_id
        record = await self._session.scalar(
            select(OrderRecord).where(
                OrderRecord.tenant_id == tenant_id, OrderRecord.id == order_id
            )
        )
        if record is None:
            return None
        lines = await self._session.scalars(
            select(OrderLineRecord)
            .where(OrderLineRecord.tenant_id == tenant_id, OrderLineRecord.order_id == order_id)
            .order_by(OrderLineRecord.position)
        )
        return _to_order(record, list(lines))

    async def save(self, order: Order) -> None:
        self._require_own(order)
        new_version = await self._session.scalar(
            update(OrderRecord)
            .where(
                OrderRecord.tenant_id == self._scope.tenant_id,
                OrderRecord.id == order.id,
                OrderRecord.version == order.version,
            )
            .values(**_status_values(order), version=OrderRecord.version + 1)
            .returning(OrderRecord.version)
        )
        if new_version is None:
            raise StaleVersionError("the order was changed by someone else; reload it")
        order.version = new_version
