"""The signed-in user's tenant: its name and the settings pricing and approvals read."""

from pricewright.application.ports import UnitOfWorkFactory
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.errors import NotFoundError
from pricewright.domain.tenants import Tenant


async def get_tenant(principal: Principal, *, unit_of_work: UnitOfWorkFactory) -> Tenant:
    principal.require(Permission.TENANT_READ)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        tenant = await uow.tenants.get(principal.tenant_id)
    if tenant is None:
        raise NotFoundError("the tenant no longer exists")
    return tenant
