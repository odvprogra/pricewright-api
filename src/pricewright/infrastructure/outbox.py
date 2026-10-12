"""The SQLAlchemy outbox (ADR-0008): events stored in the same transaction as their change, and the
relay's claims with ``FOR UPDATE SKIP LOCKED``."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from pricewright.application.ports import DeliveryOutcome, PendingEvent
from pricewright.domain.events import DeliveryStatus, Event, EventType
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


def _pending(record: OutboxEventRecord) -> PendingEvent:
    event = Event(
        id=record.id,
        tenant_id=record.tenant_id,
        type=EventType(record.type),
        aggregate_id=record.aggregate_id,
        occurred_at=record.occurred_at,
        data=dict(record.data),
        request_id=record.request_id,
    )
    return PendingEvent(event, record.attempts)


class SqlAlchemyOutboxRelay:
    """Reads and updates every tenant's events: the relay is the worker's, not a tenant's."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, now: datetime, *, limit: int) -> list[PendingEvent]:
        table, earlier = OutboxEventRecord, aliased(OutboxEventRecord)
        pending = DeliveryStatus.PENDING.value
        # An earlier pending event of the same aggregate goes first, even while another relay
        # holds it or it waits for a retry: one aggregate's events are delivered in order.
        behind_another = exists().where(
            earlier.tenant_id == table.tenant_id,
            earlier.aggregate_type == table.aggregate_type,
            earlier.aggregate_id == table.aggregate_id,
            earlier.status == pending,
            earlier.position < table.position,
        )
        records = await self._session.scalars(
            select(table)
            .where(table.status == pending, table.available_at <= now, ~behind_another)
            .order_by(table.position)
            .limit(limit)
            .with_for_update(skip_locked=True, of=table)
        )
        return [_pending(record) for record in records]

    async def record(self, event_id: UUID, outcome: DeliveryOutcome) -> None:
        values: dict[str, object] = {
            "status": outcome.status.value,
            "attempts": outcome.attempts,
            "last_error": outcome.error,
        }
        if outcome.status is DeliveryStatus.DELIVERED:
            values["delivered_at"] = outcome.at
        elif outcome.status is DeliveryStatus.PENDING:
            values["available_at"] = outcome.at
        table = OutboxEventRecord
        await self._session.execute(update(table).where(table.id == event_id).values(values))
