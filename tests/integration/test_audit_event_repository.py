"""Audit events in PostgreSQL: scoped to their tenant (ADR-0006) and append-only (ADR-0013)."""

import dataclasses
import uuid
from datetime import UTC, datetime

import pytest
import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pricewright.application.ports import AuditEventFilter
from pricewright.domain.audit import AuditAction, AuditEvent, AuditResourceType, Changes
from pricewright.domain.auth import Principal
from pricewright.domain.tenants import Tenant
from pricewright.domain.users import Role
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import Sessions, register

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
EVERYTHING = AuditEventFilter()


async def add_event(
    sessions: Sessions,
    actor: Principal,
    action: AuditAction,
    resource_id: uuid.UUID,
    changes: Changes | None = None,
) -> AuditEvent:
    event = AuditEvent.record(actor, action, resource_id, changes or {}, now=NOW)
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(actor.tenant_id)
        await uow.audit_events.add(event)
        await uow.commit()
    return event


async def page(
    sessions: Sessions, tenant: Tenant, where: AuditEventFilter, before: uuid.UUID | None = None
) -> list[AuditEvent]:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(tenant.id)
        return await uow.audit_events.page(where, before=before, limit=10)


def admin_of(tenant: Tenant) -> Principal:
    return Principal(tenant.id, uuid.uuid7(), Role.ADMIN)


async def test_audit_events_keep_their_changes_and_request_id(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    changes: Changes = {
        "full_name": (None, "Blair"),
        "role": ("sales_rep", "admin"),
        "is_active": (True, False),
        "failed_login_attempts": (100, 0),
    }

    with structlog.contextvars.bound_contextvars(request_id="req-42"):
        event = await add_event(
            session_factory, admin_of(northfield), AuditAction.USER_UPDATED, uuid.uuid7(), changes
        )

    stored = await page(session_factory, northfield, EVERYTHING)
    assert stored == [dataclasses.replace(event, request_id="req-42")]


async def test_audit_events_are_listed_newest_first_with_a_keyset(
    session_factory: Sessions,
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    admin, user_id = admin_of(northfield), uuid.uuid7()
    first = await add_event(session_factory, admin, AuditAction.USER_CREATED, user_id)
    second = await add_event(session_factory, admin, AuditAction.USER_UPDATED, user_id)
    third = await add_event(session_factory, admin, AuditAction.USER_UNLOCKED, user_id)

    everything = await page(session_factory, northfield, EVERYTHING)
    older = await page(session_factory, northfield, EVERYTHING, before=second.id)

    assert [event.id for event in everything] == [third.id, second.id, first.id]
    assert [event.id for event in older] == [first.id]


async def test_audit_events_are_filtered_by_resource_actor_and_action(
    session_factory: Sessions,
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    avery, blair = admin_of(northfield), admin_of(northfield)
    user_id, account_id = uuid.uuid7(), uuid.uuid7()
    created = await add_event(session_factory, avery, AuditAction.USER_CREATED, user_id)
    unlocked = await add_event(session_factory, blair, AuditAction.USER_UNLOCKED, user_id)
    account = await add_event(
        session_factory, blair, AuditAction.SERVICE_ACCOUNT_CREATED, account_id
    )

    by_resource = await page(
        session_factory,
        northfield,
        AuditEventFilter(resource_type=AuditResourceType.USER, resource_id=user_id),
    )
    by_type = await page(
        session_factory, northfield, AuditEventFilter(resource_type=AuditResourceType.USER)
    )
    by_actor = await page(session_factory, northfield, AuditEventFilter(actor_id=blair.subject_id))
    by_action = await page(
        session_factory, northfield, AuditEventFilter(action=AuditAction.USER_CREATED)
    )

    assert [event.id for event in by_resource] == [unlocked.id, created.id]
    assert [event.id for event in by_type] == [unlocked.id, created.id]
    assert [event.id for event in by_actor] == [account.id, unlocked.id]
    assert [event.id for event in by_action] == [created.id]


async def test_audit_events_are_visible_only_to_their_tenant(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    larkspur, _ = await register(session_factory, "Larkspur")
    await add_event(
        session_factory, admin_of(northfield), AuditAction.TENANT_UPDATED, northfield.id
    )

    assert await page(session_factory, larkspur, EVERYTHING) == []


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_events SET action = 'user.created'", "DELETE FROM audit_events"],
)
async def test_audit_events_cannot_be_changed_or_deleted(
    session_factory: Sessions, statement: str
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    await add_event(
        session_factory, admin_of(northfield), AuditAction.TENANT_UPDATED, northfield.id
    )

    async with session_factory() as session:
        with pytest.raises(IntegrityError, match="append-only"):
            await session.execute(text(statement))  # literal SQL, no input


async def test_audit_event_from_another_tenant_is_refused(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    larkspur, _ = await register(session_factory, "Larkspur")
    event = AuditEvent.record(
        admin_of(larkspur), AuditAction.TENANT_UPDATED, larkspur.id, {}, now=NOW
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        with pytest.raises(RuntimeError, match="tenant"):
            await uow.audit_events.add(event)
