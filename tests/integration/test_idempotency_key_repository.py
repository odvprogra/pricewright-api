"""Idempotency keys in PostgreSQL (ADR-0022): held by an advisory lock until the transaction ends,
scoped to the tenant and the caller, replaced only after they expire."""

import dataclasses
import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from pricewright.domain.actors import Actor
from pricewright.domain.audit import AuditResourceType
from pricewright.domain.idempotency import (
    KEY_LIFETIME,
    IdempotencyKeyInUseError,
    IdempotencyRecord,
    IdempotentRequest,
)
from pricewright.domain.tenants import Tenant
from pricewright.infrastructure.idempotency import lock_id
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import Sessions, register

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
KEY = str(uuid.uuid4())  # generated: gitleaks flags literal keys


def remembered(
    tenant: Tenant, actor: Actor, *, key: str = KEY, now: datetime = NOW
) -> IdempotencyRecord:
    return IdempotencyRecord.first(
        IdempotentRequest(key, hashlib.sha256(b'{"customer_id":"c-1"}').hexdigest()),
        tenant_id=tenant.id,
        actor=actor,
        resource_type=AuditResourceType.QUOTE,
        resource_id=uuid.uuid7(),
        now=now,
    )


async def store(sessions: Sessions, record: IdempotencyRecord) -> None:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(record.tenant_id)
        await uow.idempotency_keys.add(record)
        await uow.commit()


async def claim(
    sessions: Sessions, tenant: Tenant, actor: Actor, *, key: str = KEY, now: datetime = NOW
) -> IdempotencyRecord | None:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(tenant.id)
        return await uow.idempotency_keys.claim(actor, key, now=now)


async def test_idempotency_key_round_trips_what_the_first_request_created(
    session_factory: Sessions,
) -> None:
    tenant, (rep,) = await register(session_factory, "Northfield", "rep@northfield.example")
    actor = Actor.person(rep.id)
    first = remembered(tenant, actor)

    assert await claim(session_factory, tenant, actor) is None
    await store(session_factory, first)

    assert await claim(session_factory, tenant, actor) == first


async def test_idempotency_key_is_held_without_waiting_until_the_transaction_ends(
    session_factory: Sessions,
) -> None:
    tenant, (rep,) = await register(session_factory, "Northfield", "rep@northfield.example")
    actor = Actor.person(rep.id)

    async with SqlAlchemyUnitOfWork(session_factory) as first:
        first.bind_tenant(tenant.id)
        assert await first.idempotency_keys.claim(actor, KEY, now=NOW) is None
        # The first request is still running: a retry is refused at once, never blocked.
        async with SqlAlchemyUnitOfWork(session_factory) as retry:
            retry.bind_tenant(tenant.id)
            with pytest.raises(IdempotencyKeyInUseError, match="in progress"):
                await retry.idempotency_keys.claim(actor, KEY, now=NOW)
            with pytest.raises(IdempotencyKeyInUseError):
                await retry.idempotency_keys.add(remembered(tenant, actor))
        await first.idempotency_keys.add(remembered(tenant, actor))
        await first.commit()
        # Committing ended the transaction and its lock: the next request reads what it stored.
        assert await claim(session_factory, tenant, actor) is not None


async def test_idempotency_key_is_released_and_forgotten_on_rollback(
    session_factory: Sessions,
) -> None:
    tenant, (rep,) = await register(session_factory, "Northfield", "rep@northfield.example")
    actor = Actor.person(rep.id)

    async with SqlAlchemyUnitOfWork(session_factory) as failed:
        failed.bind_tenant(tenant.id)
        await failed.idempotency_keys.add(remembered(tenant, actor))

    assert await claim(session_factory, tenant, actor) is None


async def test_idempotency_keys_belong_to_their_caller_and_tenant(
    session_factory: Sessions,
) -> None:
    northfield, (rep, other_rep) = await register(
        session_factory, "Northfield", "rep@northfield.example", "other@northfield.example"
    )
    larkspur, (larkspur_rep,) = await register(session_factory, "Larkspur", "rep@larkspur.example")
    actor, other = Actor.person(rep.id), Actor.person(other_rep.id)
    await store(session_factory, remembered(northfield, actor))

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        await uow.idempotency_keys.claim(actor, KEY, now=NOW)
        # Another caller's key with the same value is another key: neither seen nor locked.
        assert await claim(session_factory, northfield, other) is None
    assert await claim(session_factory, larkspur, actor) is None
    assert await claim(session_factory, larkspur, Actor.person(larkspur_rep.id)) is None
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(larkspur.id)
        with pytest.raises(RuntimeError, match="unit of work's tenant"):
            await uow.idempotency_keys.add(remembered(northfield, actor))


async def test_idempotency_key_is_replaced_only_after_it_expires(
    session_factory: Sessions,
) -> None:
    tenant, (rep,) = await register(session_factory, "Northfield", "rep@northfield.example")
    actor = Actor.person(rep.id)
    first = remembered(tenant, actor)
    await store(session_factory, first)
    later = NOW + KEY_LIFETIME

    with pytest.raises(RuntimeError, match="unexpired"):
        await store(session_factory, remembered(tenant, actor, now=later - timedelta(seconds=1)))
    assert await claim(session_factory, tenant, actor, now=later) is None
    renewed = remembered(tenant, actor, now=later)
    await store(session_factory, renewed)

    assert await claim(session_factory, tenant, actor, now=later) == renewed


@pytest.mark.parametrize(
    "break_it",
    [
        lambda record: dataclasses.replace(record, fingerprint="not a sha-256"),
        lambda record: dataclasses.replace(record, key=""),
        lambda record: dataclasses.replace(record, expires_at=record.created_at),
    ],
    ids=["fingerprint", "empty key", "expiry"],
)
async def test_idempotency_keys_table_checks_what_the_domain_guarantees(
    session_factory: Sessions, break_it: Callable[[IdempotencyRecord], IdempotencyRecord]
) -> None:
    tenant, (rep,) = await register(session_factory, "Northfield", "rep@northfield.example")
    broken = break_it(remembered(tenant, Actor.person(rep.id)))

    with pytest.raises(IntegrityError):
        await store(session_factory, broken)


def test_idempotency_key_lock_ids_differ_by_tenant_caller_and_key() -> None:
    tenant, actor = uuid.uuid7(), Actor.person(uuid.uuid7())
    ids = {
        lock_id(tenant, actor, KEY),
        lock_id(uuid.uuid7(), actor, KEY),
        lock_id(tenant, Actor.person(uuid.uuid7()), KEY),
        lock_id(tenant, actor, KEY + "x"),
    }

    assert len(ids) == 4
    assert all(-(2**63) <= value < 2**63 for value in ids)  # a PostgreSQL bigint
