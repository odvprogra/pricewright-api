"""Managing a tenant's users (admins)."""

from uuid import UUID

from pricewright.application.pagination import Page
from pricewright.application.ports import UnitOfWorkFactory
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.errors import NotFoundError
from pricewright.domain.users import User


async def list_users(
    principal: Principal, *, after: UUID | None, limit: int, unit_of_work: UnitOfWorkFactory
) -> Page[User]:
    principal.require(Permission.USERS_MANAGE)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        users = await uow.users.list(after=after, limit=limit + 1)  # one extra: is there more?
    page = users[:limit]
    has_more = len(users) > limit
    return Page(items=page, next_after=page[-1].id if has_more else None)


async def get_user(principal: Principal, user_id: UUID, *, unit_of_work: UnitOfWorkFactory) -> User:
    """A user of the caller's tenant. Another tenant's user does not exist here (ADR-0009)."""
    principal.require(Permission.USERS_MANAGE)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        user = await uow.users.get(user_id)
    if user is None:
        raise NotFoundError("no such user")
    return user
