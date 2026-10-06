"""Idempotent creations (ADR-0022): a retry with the same key gets what the first request created.

A use case that creates something checks the key first, inside its unit of work, and remembers it
in the same unit of work as the creation, so the key commits or rolls back with the change.
"""

from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pricewright.application.ports import UnitOfWork
from pricewright.domain.actors import Actor
from pricewright.domain.audit import AuditResourceType
from pricewright.domain.auth import Principal
from pricewright.domain.errors import NotFoundError
from pricewright.domain.idempotency import IdempotencyRecord, IdempotentRequest


@dataclass(frozen=True, slots=True)
class Created[T]:
    """What a creation returns: the resource, and whether a retry got it back instead."""

    value: T
    replayed: bool = False


async def earlier_creation(
    uow: UnitOfWork, principal: Principal, request: IdempotentRequest | None, *, now: datetime
) -> UUID | None:
    """The resource an earlier request with the same key created, if this request repeats it.

    The key stays held until the unit of work ends. Raise ``IdempotencyKeyInUseError`` while
    another request holds it, and ``IdempotencyKeyReusedError`` for a different request.
    """
    if request is None:
        return None
    record = await uow.idempotency_keys.claim(Actor.of(principal), request.key, now=now)
    return None if record is None else record.replay(request)


async def replay[T](found: Awaitable[T | None]) -> Created[T]:
    """What the first request created, as it is now (ADR-0022)."""
    value = await found
    if value is None:
        raise NotFoundError("what this Idempotency-Key created no longer exists")
    return Created(value, replayed=True)


async def remember_creation(
    uow: UnitOfWork,
    principal: Principal,
    request: IdempotentRequest | None,
    resource_type: AuditResourceType,
    resource_id: UUID,
    *,
    now: datetime,
) -> None:
    """Remember what ``request`` created, in the unit of work that creates it."""
    if request is None:
        return
    await uow.idempotency_keys.add(
        IdempotencyRecord.first(
            request,
            tenant_id=principal.tenant_id,
            actor=Actor.of(principal),
            resource_type=resource_type,
            resource_id=resource_id,
            now=now,
        )
    )
