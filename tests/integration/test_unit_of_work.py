from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pricewright.application.ports import UnitOfWork
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.infrastructure.records import TenantRecord
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration

type Sessions = async_sessionmaker[AsyncSession]


def northfield() -> Tenant:
    return Tenant.register(
        name="Northfield Supply",
        settings=TenantSettings(currency="USD", tax_rate=Decimal("0.0725")),
    )


async def test_unit_of_work_commit_persists_the_tenant(session_factory: Sessions) -> None:
    tenant = northfield()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        await uow.tenants.add(tenant)
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert await uow.tenants.get(tenant.id) == tenant


async def test_unit_of_work_without_commit_discards_the_changes(session_factory: Sessions) -> None:
    tenant = northfield()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        await uow.tenants.add(tenant)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert await uow.tenants.get(tenant.id) is None


async def test_unit_of_work_discards_the_changes_when_the_operation_fails(
    session_factory: Sessions,
) -> None:
    tenant = northfield()

    async def add_then_fail() -> None:
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            await uow.tenants.add(tenant)
            await uow.tenants.get(tenant.id)  # flushes the insert
            raise LookupError

    with pytest.raises(LookupError):
        await add_then_fail()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert await uow.tenants.get(tenant.id) is None


async def test_unit_of_work_outside_its_context_refuses_to_commit(
    session_factory: Sessions,
) -> None:
    uow: UnitOfWork = SqlAlchemyUnitOfWork(session_factory)  # also checks it satisfies the port

    with pytest.raises(RuntimeError, match="async with"):
        await uow.commit()


async def test_database_rejects_tenant_settings_the_domain_forbids(
    session_factory: Sessions,
) -> None:
    async with session_factory() as session:
        session.add(TenantRecord(name="Bad", currency="usd", tax_rate=1, approval_threshold=2))

        with pytest.raises(IntegrityError, match="ck_tenants_"):
            await session.commit()
