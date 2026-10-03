"""Managing a tenant's users (admins)."""

from dataclasses import dataclass, field
from uuid import UUID

from pricewright.application.pagination import Page
from pricewright.application.ports import PasswordHasher, UnitOfWork, UnitOfWorkFactory
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.errors import NotFoundError
from pricewright.domain.users import (
    EmailAlreadyRegisteredError,
    Role,
    User,
    normalize_email,
    normalize_full_name,
    normalize_password,
)


@dataclass(frozen=True, slots=True)
class NewUser:
    email: str
    full_name: str
    role: Role
    password: str = field(repr=False)


async def prepare_user(tenant_id: UUID, new_user: NewUser, hasher: PasswordHasher) -> User:
    """Validate everything first: hashing is the costly step."""
    email = normalize_email(new_user.email)
    full_name = normalize_full_name(new_user.full_name)
    password = normalize_password(new_user.password)
    return User.create(
        tenant_id=tenant_id,
        email=email,
        full_name=full_name,
        role=new_user.role,
        password_hash=await hasher.hash(password),
    )


async def ensure_email_is_free(uow: UnitOfWork, email: str) -> None:
    """Emails are unique across all tenants (ADR-0007)."""
    if await uow.identities.user_by_email(email) is not None:
        raise EmailAlreadyRegisteredError(f"{email} is already registered")


async def create_user(
    principal: Principal,
    new_user: NewUser,
    *,
    unit_of_work: UnitOfWorkFactory,
    hasher: PasswordHasher,
) -> User:
    principal.require(Permission.USERS_MANAGE)
    user = await prepare_user(principal.tenant_id, new_user, hasher)
    async with unit_of_work() as uow:
        await ensure_email_is_free(uow, user.email)
        uow.bind_tenant(principal.tenant_id)
        await uow.users.add(user)
        await uow.commit()
    return user


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
