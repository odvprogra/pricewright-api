"""A tenant always keeps one active admin, even when two admins demote each other at once."""

import asyncio
import uuid

import pytest

from pricewright.application.ports import UnitOfWork
from pricewright.application.users import UserChanges, change_user
from pricewright.domain.auth import Principal
from pricewright.domain.users import LastAdminError, Role, User
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import Sessions, register

pytestmark = pytest.mark.integration


async def test_two_admins_demoting_each_other_at_once_leave_one_admin(
    session_factory: Sessions,
) -> None:
    northfield, admins = await register(
        session_factory, "Northfield", "avery@northfield.example", "blair@northfield.example"
    )
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        for admin in admins:
            admin.change(role=Role.ADMIN)
            await uow.users.save(admin)
        await uow.commit()

    def unit_of_work() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    async def demote(target: User) -> str:
        try:
            await change_user(
                Principal(northfield.id, uuid.uuid7(), Role.ADMIN),
                target.id,
                UserChanges(role=Role.SALES_REP),
                expected_version=target.version,
                unit_of_work=unit_of_work,
            )
        except LastAdminError:
            return "refused"
        return "demoted"

    results = await asyncio.gather(demote(admins[0]), demote(admins[1]))

    assert sorted(results) == ["demoted", "refused"]
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        assert len(await uow.users.lock_active_admins()) == 1
