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


VALID_TENANT: dict[str, object] = {
    "name": "Northfield",
    "currency": "USD",
    "tax_rate": Decimal("0.0725"),
    "approval_threshold": Decimal("0.15"),
    "quote_prefix": "NF",
    "quote_validity_days": 30,
    "order_prefix": "NFO",
}


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("currency", "usd", "currency_is_iso_4217"),
        ("tax_rate", 1, "tax_rate_in_range"),
        ("approval_threshold", 2, "approval_threshold_in_range"),
        ("quote_prefix", "N-F", "quote_prefix_is_valid"),
        ("quote_validity_days", 0, "quote_validity_days_in_range"),
        ("order_prefix", "nfo", "order_prefix_is_valid"),
        ("order_prefix", "NF", "prefixes_differ"),
    ],
)
async def test_database_rejects_tenant_settings_the_domain_forbids(
    session_factory: Sessions, field: str, value: object, constraint: str
) -> None:
    async with session_factory() as session:
        session.add(TenantRecord(**(VALID_TENANT | {field: value})))

        with pytest.raises(IntegrityError, match=f"ck_tenants_{constraint}"):
            await session.commit()
