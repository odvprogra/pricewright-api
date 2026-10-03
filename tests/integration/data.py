"""Builders that save test data through the real unit of work."""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role, User
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork

type Sessions = async_sessionmaker[AsyncSession]

ADMIN_EMAIL = "avery@northfield.example"
ADMIN_PASSWORD = "northfield admin passphrase"


async def register(sessions: Sessions, name: str, *emails: str) -> tuple[Tenant, list[User]]:
    """A tenant with one sales rep per email."""
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
