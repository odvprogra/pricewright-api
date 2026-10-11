"""The SQLAlchemy outbox (ADR-0008): events stored in the same transaction as their change."""

from sqlalchemy.ext.asyncio import AsyncSession

from pricewright.domain.events import Event
from pricewright.infrastructure.logging import current_request_id
from pricewright.infrastructure.records import OutboxEventRecord
from pricewright.infrastructure.repositories import TenantScope


class SqlAlchemyOutboxRepository:
    def __init__(self, session: AsyncSession, scope: TenantScope) -> None:
        self._session = session
        self._scope = scope

    async def add(self, event: Event) -> None:
        if event.tenant_id != self._scope.tenant_id:
            raise RuntimeError("an event can only be added to the unit of work's tenant")
        self._session.add(
            OutboxEventRecord(
                id=event.id,
                tenant_id=event.tenant_id,
                type=event.type.value,
                aggregate_type=event.aggregate_type.value,
                aggregate_id=event.aggregate_id,
                occurred_at=event.occurred_at,
                data=dict(event.data),
                request_id=event.request_id or current_request_id(),
                available_at=event.occurred_at,
            )
        )
