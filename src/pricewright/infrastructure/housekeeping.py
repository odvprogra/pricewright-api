"""The SQLAlchemy housekeeping reads: what the worker's jobs find to do across tenants (ADR-0026).

They return ids only. Whatever a job then changes, it changes in a unit of work bound to the
record's tenant, through the tenant's own repositories (ADR-0006).
"""

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pricewright.application.ports import TenantRecordRef
from pricewright.domain.quote_lifecycle import EXPIRABLE
from pricewright.infrastructure.records import QuoteRecord


class SqlAlchemyHousekeeping:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def offers_past_validity(
        self, today: date, *, after: UUID | None, limit: int
    ) -> list[TenantRecordRef]:
        table = QuoteRecord
        query = (
            select(table.tenant_id, table.id)
            .where(table.status.in_([status.value for status in EXPIRABLE]))
            .where(table.valid_until < today)
            .order_by(table.id)
            .limit(limit)
        )
        if after is not None:
            query = query.where(table.id > after)
        rows = await self._session.execute(query)
        return [TenantRecordRef(tenant_id, quote_id) for tenant_id, quote_id in rows]
