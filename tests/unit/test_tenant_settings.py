import uuid
from decimal import Decimal

import pytest

from pricewright.application.ports import UnitOfWork
from pricewright.application.tenants import get_tenant
from pricewright.domain.auth import Principal
from pricewright.domain.errors import NotFoundError
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeUnitOfWork, InMemoryDatabase

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
