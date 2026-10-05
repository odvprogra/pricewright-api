import copy
import uuid
from decimal import Decimal

import pytest

from pricewright.application.ports import UnitOfWork
from pricewright.application.tenants import TenantChanges, change_tenant, get_tenant
from pricewright.domain.audit import ActorType, AuditAction
from pricewright.domain.auth import PermissionDeniedError, Principal
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeClock, FakeUnitOfWork, InMemoryDatabase

NORTHFIELD = Tenant.register(
    name="Northfield Supply", settings=TenantSettings("USD", Decimal("0.0725"))
)


@pytest.mark.parametrize("role", list(Role))
async def test_get_tenant_returns_the_callers_tenant_for_every_role(role: Role) -> None:
    database = InMemoryDatabase(tenants={NORTHFIELD.id: NORTHFIELD})

    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(database)

    tenant = await get_tenant(
        Principal(NORTHFIELD.id, uuid.uuid7(), role), unit_of_work=unit_of_work
    )

    assert tenant == NORTHFIELD


async def test_get_tenant_of_a_tenant_that_no_longer_exists_is_not_found() -> None:
    database = InMemoryDatabase()

    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(database)

    with pytest.raises(NotFoundError):
        await get_tenant(
            Principal(uuid.uuid7(), uuid.uuid7(), Role.ADMIN), unit_of_work=unit_of_work
        )


async def change(
    database: InMemoryDatabase,
    role: Role,
    changes: TenantChanges,
    version: int = 1,
    principal: Principal | None = None,
) -> Tenant:
    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(database)

    return await change_tenant(
        principal or Principal(NORTHFIELD.id, uuid.uuid7(), role),
        changes,
        expected_version=version,
        unit_of_work=unit_of_work,
        clock=FakeClock(),
    )


async def test_change_tenant_by_an_admin_saves_a_new_version() -> None:
    database = InMemoryDatabase(tenants={NORTHFIELD.id: copy.deepcopy(NORTHFIELD)})

    changed = await change(database, Role.ADMIN, TenantChanges(tax_rate=Decimal("0.08")))

    assert changed.version == 2
    assert database.tenants[NORTHFIELD.id].settings.tax_rate == Decimal("0.08")


@pytest.mark.parametrize("role", [Role.SALES_REP, Role.SALES_MANAGER])
async def test_change_tenant_by_a_non_admin_is_denied(role: Role) -> None:
    database = InMemoryDatabase(tenants={NORTHFIELD.id: copy.deepcopy(NORTHFIELD)})

    with pytest.raises(PermissionDeniedError):
        await change(database, role, TenantChanges(name="Hijacked"))

    assert database.tenants[NORTHFIELD.id].name == "Northfield Supply"


async def test_change_tenant_based_on_an_old_version_is_rejected() -> None:
    database = InMemoryDatabase(tenants={NORTHFIELD.id: copy.deepcopy(NORTHFIELD)})
    await change(database, Role.ADMIN, TenantChanges(name="First edit"))

    with pytest.raises(StaleVersionError):
        await change(database, Role.ADMIN, TenantChanges(name="Second edit"), version=1)

    assert database.tenants[NORTHFIELD.id].name == "First edit"
    assert len(database.audit_events) == 1  # the rejected edit left no event


async def test_change_tenant_that_no_longer_exists_is_not_found() -> None:
    with pytest.raises(NotFoundError):
        await change(InMemoryDatabase(), Role.ADMIN, TenantChanges(name="Ghost"))


async def test_change_tenant_records_who_changed_which_settings() -> None:
    database = InMemoryDatabase(tenants={NORTHFIELD.id: copy.deepcopy(NORTHFIELD)})
    admin = Principal(NORTHFIELD.id, uuid.uuid7(), Role.ADMIN)

    await change(
        database,
        Role.ADMIN,
        TenantChanges(name="Northfield Supply", tax_rate=Decimal("0.08")),
        principal=admin,
    )

    [event] = database.audit_events.values()
    assert event.action is AuditAction.TENANT_UPDATED
    assert (event.actor_type, event.actor_id) == (ActorType.USER, admin.subject_id)
    assert event.resource_id == NORTHFIELD.id
    assert event.changes == {"tax_rate": ("0.0725", "0.0800")}  # the unchanged name is left out


async def test_change_tenant_records_new_quote_settings() -> None:
    database = InMemoryDatabase(tenants={NORTHFIELD.id: copy.deepcopy(NORTHFIELD)})

    changed = await change(
        database, Role.ADMIN, TenantChanges(quote_prefix="NF", quote_validity_days=45)
    )

    assert (changed.settings.quote_prefix, changed.settings.quote_validity_days) == ("NF", 45)
    [event] = database.audit_events.values()
    assert event.changes == {"quote_prefix": ("QUO", "NF"), "quote_validity_days": (30, 45)}


async def test_change_tenant_records_a_new_order_prefix() -> None:
    database = InMemoryDatabase(tenants={NORTHFIELD.id: copy.deepcopy(NORTHFIELD)})

    changed = await change(database, Role.ADMIN, TenantChanges(order_prefix="NFO"))

    assert changed.settings.order_prefix == "NFO"
    [event] = database.audit_events.values()
    assert event.changes == {"order_prefix": ("ORD", "NFO")}


async def test_change_tenant_that_changes_nothing_records_no_event() -> None:
    stored = copy.deepcopy(NORTHFIELD)
    stored.change(tax_rate=Decimal("0.0800"))  # as PostgreSQL returns NUMERIC(5, 4)
    database = InMemoryDatabase(tenants={NORTHFIELD.id: stored})

    await change(
        database, Role.ADMIN, TenantChanges(name="Northfield Supply", tax_rate=Decimal("0.08"))
    )

    assert database.audit_events == {}
