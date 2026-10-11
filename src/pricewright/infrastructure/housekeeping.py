"""The SQLAlchemy housekeeping: what the worker's jobs do across tenants (ADR-0026).

Searches return ids only: whatever a job then changes, it changes in a unit of work bound to the
record's tenant, through the tenant's own repositories (ADR-0006). Purges delete expired technical
records in batches, each in a short transaction of its own.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from pricewright.application.ports import TenantRecordRef
from pricewright.domain.quote_lifecycle import EXPIRABLE
from pricewright.infrastructure.records import IdempotencyKeyRecord, QuoteRecord


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

    async def purge_idempotency_keys(self, now: datetime, *, limit: int) -> int:
        table = IdempotencyKeyRecord
        columns = (table.tenant_id, table.actor_type, table.actor_id, table.idempotency_key)
        expired = select(*columns).where(table.expires_at <= now).limit(limit)
        purged = await self._session.scalars(
            delete(table)
            .where(tuple_(*columns).in_(expired))
            # Checked again on each row it deletes: a request that renewed the key meanwhile
            # gave it a new expiry, and PostgreSQL re-reads a row a committed update changed.
            .where(table.expires_at <= now)
            .returning(table.tenant_id)
        )
        return len(purged.all())
