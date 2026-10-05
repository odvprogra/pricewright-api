"""The signed-in user's tenant: its name and the settings pricing and approvals read."""

from dataclasses import dataclass
from decimal import Decimal

from pricewright.application.audit import record, tenant_fields
from pricewright.application.ports import Clock, UnitOfWorkFactory
from pricewright.domain.audit import AuditAction, changed
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.tenants import Tenant


async def get_tenant(principal: Principal, *, unit_of_work: UnitOfWorkFactory) -> Tenant:
    principal.require(Permission.TENANT_READ)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        tenant = await uow.tenants.get(principal.tenant_id)
    if tenant is None:
        raise NotFoundError("the tenant no longer exists")
    return tenant


@dataclass(frozen=True, slots=True)
class TenantChanges:
    """Fields left as ``None`` keep their value."""

    name: str | None = None
    tax_rate: Decimal | None = None
    approval_threshold: Decimal | None = None
    quote_prefix: str | None = None
    quote_validity_days: int | None = None


async def change_tenant(
    principal: Principal,
    changes: TenantChanges,
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> Tenant:
    """Apply the changes if the tenant is still at ``expected_version`` (ADR-0012)."""
    principal.require(Permission.TENANT_MANAGE)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        tenant = await uow.tenants.get(principal.tenant_id)
        if tenant is None:
            raise NotFoundError("the tenant no longer exists")
        if tenant.version != expected_version:
            raise StaleVersionError("the tenant was changed by someone else; reload it")
        before = tenant_fields(tenant)
        tenant.change(
            name=changes.name,
            tax_rate=changes.tax_rate,
            approval_threshold=changes.approval_threshold,
            quote_prefix=changes.quote_prefix,
            quote_validity_days=changes.quote_validity_days,
        )
        await uow.tenants.save(tenant)
        await record(
            uow,
            principal,
            AuditAction.TENANT_UPDATED,
            tenant.id,
            changed(before, tenant_fields(tenant)),
            now=clock(),
        )
        await uow.commit()
    return tenant
