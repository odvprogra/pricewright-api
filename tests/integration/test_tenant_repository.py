"""Saving tenants is compare-and-set on the version (ADR-0012)."""

import asyncio
from decimal import Decimal

import pytest

from pricewright.domain.errors import StaleVersionError
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import Sessions, register

pytestmark = pytest.mark.integration


async def test_tenant_repository_save_stores_changes_and_bumps_the_version(
    session_factory: Sessions,
) -> None:
    northfield, _ = await register(session_factory, "Northfield")

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        tenant = await uow.tenants.get(northfield.id)
        assert tenant is not None
        tenant.change(
            name="Northfield Supply",
            tax_rate=Decimal("0.0725"),
            quote_prefix="NF",
            quote_validity_days=45,
            order_prefix="NFO",
        )
        await uow.tenants.save(tenant)
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        stored = await uow.tenants.get(northfield.id)
    assert stored == tenant
    assert stored.version == 2


async def test_tenant_repository_refuses_to_save_over_a_newer_version(
    session_factory: Sessions,
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    northfield.version = 0  # pretend it was read before an earlier save

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        with pytest.raises(StaleVersionError):
            await uow.tenants.save(northfield)


async def test_concurrent_saves_of_the_same_version_let_exactly_one_win(
    session_factory: Sessions,
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    both_have_read = asyncio.Barrier(2)

    async def rename(name: str) -> str:
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            tenant = await uow.tenants.get(northfield.id)
            assert tenant is not None
            await both_have_read.wait()  # both edit the same version, whatever the scheduling
            tenant.change(name=name)
            try:
                await uow.tenants.save(tenant)
            except StaleVersionError:
                return "stale"
            await uow.commit()
            return "saved"

    results = await asyncio.gather(rename("First"), rename("Second"))

    assert sorted(results) == ["saved", "stale"]
