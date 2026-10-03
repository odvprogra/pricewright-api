"""Onboarding: register a distributor as a tenant, together with its first administrator."""

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from pricewright.application.ports import PasswordHasher, UnitOfWorkFactory
from pricewright.application.users import NewUser, ensure_email_is_free, prepare_user
from pricewright.domain.tenants import DEFAULT_APPROVAL_THRESHOLD, Tenant, TenantSettings
from pricewright.domain.users import Role


@dataclass(frozen=True, slots=True)
class RegisterTenant:
    name: str
    currency: str
    tax_rate: Decimal
    admin_email: str
    admin_full_name: str
    admin_password: str = field(repr=False)
    approval_threshold: Decimal = DEFAULT_APPROVAL_THRESHOLD


@dataclass(frozen=True, slots=True)
class RegisteredTenant:
    tenant_id: UUID
    admin_id: UUID


async def register_tenant(
    command: RegisterTenant, *, unit_of_work: UnitOfWorkFactory, hasher: PasswordHasher
) -> RegisteredTenant:
    """Create the tenant and its admin atomically. Input is validated before the costly hash."""
    tenant = Tenant.register(
        name=command.name,
        settings=TenantSettings(
            currency=command.currency,
            tax_rate=command.tax_rate,
            approval_threshold=command.approval_threshold,
        ),
    )
    admin = await prepare_user(
        tenant.id,
        NewUser(
            email=command.admin_email,
            full_name=command.admin_full_name,
            role=Role.ADMIN,
            password=command.admin_password,
        ),
        hasher,
    )

    async with unit_of_work() as uow:
        await ensure_email_is_free(uow, admin.email)
        uow.bind_tenant(tenant.id)
        await uow.tenants.add(tenant)
        await uow.users.add(admin)
        await uow.commit()

    return RegisteredTenant(tenant_id=tenant.id, admin_id=admin.id)
