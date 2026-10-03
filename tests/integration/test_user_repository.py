"""Users are reachable only through their tenant's unit of work (ADR-0006)."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pricewright.domain.errors import ConflictError
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role, User
from pricewright.infrastructure.records import UserRecord
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration

type Sessions = async_sessionmaker[AsyncSession]


async def register(sessions: Sessions, name: str, *emails: str) -> tuple[Tenant, list[User]]:
    tenant = Tenant.register(name=name, settings=TenantSettings("USD", Decimal("0.07")))
    users = [
        User.create(
            tenant_id=tenant.id,
            email=email,
            full_name=email.split("@")[0],
            role=Role.SALES_REP,
            password_hash="hash",
        )
        for email in emails
    ]
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(tenant.id)
        await uow.tenants.add(tenant)
        for user in users:
            await uow.users.add(user)
        await uow.commit()
    return tenant, users


async def test_user_repository_returns_users_of_the_bound_tenant(
    session_factory: Sessions,
) -> None:
    northfield, [avery] = await register(session_factory, "Northfield", "avery@northfield.example")

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)

        assert await uow.users.get(avery.id) == avery


async def test_user_repository_hides_users_of_another_tenant(session_factory: Sessions) -> None:
    _, [avery] = await register(session_factory, "Northfield", "avery@northfield.example")
    larkspur, _ = await register(session_factory, "Larkspur")

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(larkspur.id)

        assert await uow.users.get(avery.id) is None


async def test_user_repository_refuses_a_user_of_another_tenant(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    larkspur, _ = await register(session_factory, "Larkspur")
    intruder = User.create(
        tenant_id=northfield.id,
        email="intruder@larkspur.example",
        full_name="Intruder",
        role=Role.ADMIN,
        password_hash="hash",
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(larkspur.id)

        with pytest.raises(RuntimeError, match="unit of work's tenant"):
            await uow.users.add(intruder)


async def test_unit_of_work_refuses_tenant_owned_access_before_binding(
    session_factory: Sessions,
) -> None:
    _, [avery] = await register(session_factory, "Northfield", "avery@northfield.example")

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        with pytest.raises(RuntimeError, match="before the unit of work was bound"):
            await uow.users.get(avery.id)


async def test_unit_of_work_cannot_be_bound_to_a_second_tenant(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    larkspur, _ = await register(session_factory, "Larkspur")

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        uow.bind_tenant(northfield.id)  # binding again to the same tenant is harmless

        with pytest.raises(RuntimeError, match="another tenant"):
            uow.bind_tenant(larkspur.id)


async def test_identity_lookup_finds_a_user_by_email_in_any_tenant(
    session_factory: Sessions,
) -> None:
    _, [avery] = await register(session_factory, "Northfield", "avery@northfield.example")

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert await uow.identities.user_by_email("avery@northfield.example") == avery
        assert await uow.identities.user_by_email("nobody@northfield.example") is None


async def test_unit_of_work_reports_a_duplicate_email_as_a_conflict(
    session_factory: Sessions,
) -> None:
    await register(session_factory, "Northfield", "avery@northfield.example")

    with pytest.raises(ConflictError):
        await register(session_factory, "Larkspur", "avery@northfield.example")


async def test_database_rejects_an_uppercase_email_or_unknown_role(
    session_factory: Sessions,
) -> None:
    _, [avery] = await register(session_factory, "Northfield", "avery@northfield.example")

    for change in ({"email": "Avery@Northfield.Example"}, {"role": "owner"}):
        async with session_factory() as session:
            with pytest.raises(IntegrityError, match="ck_users_"):
                await session.execute(
                    update(UserRecord).where(UserRecord.id == avery.id).values(**change)
                )


async def test_unit_of_work_reraises_integrity_errors_other_than_duplicates(
    session_factory: Sessions,
) -> None:
    orphan = User.create(
        tenant_id=uuid.uuid7(),  # no such tenant: the foreign key rejects it
        email="orphan@nowhere.example",
        full_name="Orphan",
        role=Role.SALES_REP,
        password_hash="hash",
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(orphan.tenant_id)
        await uow.users.add(orphan)

        with pytest.raises(IntegrityError, match="fk_users_tenant_id_tenants"):
            await uow.commit()


async def test_user_repository_saves_changes_to_a_user(session_factory: Sessions) -> None:
    northfield, [avery] = await register(session_factory, "Northfield", "avery@northfield.example")
    avery.record_failed_login()
    avery.role = Role.SALES_MANAGER

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        await uow.users.save(avery)
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        assert await uow.users.get(avery.id) == avery


async def test_user_repository_cannot_save_a_user_of_another_tenant(
    session_factory: Sessions,
) -> None:
    _, [avery] = await register(session_factory, "Northfield", "avery@northfield.example")
    larkspur, _ = await register(session_factory, "Larkspur")
    avery.is_active = False

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(larkspur.id)

        with pytest.raises(RuntimeError, match="existing user of the unit of work's tenant"):
            await uow.users.save(avery)
