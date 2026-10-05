"""The SQLAlchemy idempotency key repository (ADR-0022).

A key is held with a transaction-level advisory lock, taken without waiting: a second request with
the same key gets ``IdempotencyKeyInUseError`` at once instead of a connection blocked until the
first one commits. PostgreSQL releases the lock when the transaction ends, after its commit is
visible, so the next holder reads what the first request stored.
"""

import hashlib
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pricewright.domain.actors import Actor
from pricewright.domain.audit import ActorType, AuditResourceType
from pricewright.domain.idempotency import IdempotencyKeyInUseError, IdempotencyRecord
from pricewright.infrastructure.records import IdempotencyKeyRecord
from pricewright.infrastructure.repositories import TenantScope


def lock_id(tenant_id: UUID, actor: Actor, key: str) -> int:
    """The advisory lock of a key: 64 bits of a SHA-256 of its tenant, caller and value.

    Two keys share a lock only if their hashes collide; the cost is a needless "in use" answer.
    """
    digest = hashlib.sha256(f"{tenant_id}:{actor.type}:{actor.id}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _to_record(record: IdempotencyKeyRecord) -> IdempotencyRecord:
    return IdempotencyRecord(
        tenant_id=record.tenant_id,
        actor=Actor(ActorType(record.actor_type), record.actor_id),
        key=record.idempotency_key,
        fingerprint=record.fingerprint,
        resource_type=AuditResourceType(record.resource_type),
        resource_id=record.resource_id,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


class SqlAlchemyIdempotencyKeyRepository:
    def __init__(self, session: AsyncSession, scope: TenantScope) -> None:
        self._session = session
        self._scope = scope

    async def claim(self, actor: Actor, key: str, *, now: datetime) -> IdempotencyRecord | None:
        await self._hold(actor, key)
        table = IdempotencyKeyRecord
        record = await self._session.scalar(
            select(table).where(
                table.tenant_id == self._scope.tenant_id,
                table.actor_type == actor.type.value,
                table.actor_id == actor.id,
                table.idempotency_key == key,
                table.expires_at > now,
            )
        )
        return None if record is None else _to_record(record)

    async def add(self, record: IdempotencyRecord) -> None:
        if record.tenant_id != self._scope.tenant_id:
            raise RuntimeError("an idempotency key can only be added to the unit of work's tenant")
        await self._hold(record.actor, record.key)
        table = IdempotencyKeyRecord
        remembered = {
            "fingerprint": record.fingerprint,
            "resource_type": record.resource_type.value,
            "resource_id": record.resource_id,
            "created_at": record.created_at,
            "expires_at": record.expires_at,
        }
        stored = await self._session.scalar(
            pg_insert(table)
            .values(
                tenant_id=record.tenant_id,
                actor_type=record.actor.type.value,
                actor_id=record.actor.id,
                idempotency_key=record.key,
                **remembered,
            )
            .on_conflict_do_update(
                index_elements=[
                    table.tenant_id,
                    table.actor_type,
                    table.actor_id,
                    table.idempotency_key,
                ],
                set_=remembered,
                # Only an expired record makes way; a live one means the caller skipped claim.
                where=table.expires_at <= record.created_at,
            )
            .returning(table.idempotency_key)
        )
        if stored is None:
            raise RuntimeError("this Idempotency-Key already remembers an unexpired request")

    async def _hold(self, actor: Actor, key: str) -> None:
        lock = lock_id(self._scope.tenant_id, actor, key)
        if not await self._session.scalar(select(func.pg_try_advisory_xact_lock(lock))):
            raise IdempotencyKeyInUseError(
                "a request with this Idempotency-Key is still in progress; retry once it finishes"
            )
