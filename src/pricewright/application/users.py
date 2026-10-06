"""Managing a tenant's users (admins)."""

from dataclasses import dataclass, field
from uuid import UUID

from pricewright.application.audit import record, user_fields
from pricewright.application.idempotency import (
    Created,
    earlier_creation,
    remember_creation,
    replay,
)
from pricewright.application.pagination import Keyset, Page, page_of
from pricewright.application.ports import Clock, PasswordHasher, UnitOfWork, UnitOfWorkFactory
from pricewright.domain.audit import AuditAction, AuditResourceType, changed, created
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.idempotency import IdempotentRequest
from pricewright.domain.users import (
    EmailAlreadyRegisteredError,
    LastAdminError,
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
    clock: Clock,
    idempotency: IdempotentRequest | None = None,
) -> Created[User]:
    """A retry with the same ``Idempotency-Key`` gets the user back (ADR-0022). The password is
    not part of the request's fingerprint: a hash of it would be a fast hash of a secret."""
    principal.require(Permission.USERS_MANAGE)
    now = clock()
    user = await prepare_user(principal.tenant_id, new_user, hasher)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        if (earlier := await earlier_creation(uow, principal, idempotency, now=now)) is not None:
            return await replay(uow.users.get(earlier))
        await ensure_email_is_free(uow, user.email)
        await uow.users.add(user)
        changes = created(user_fields(user))
        await record(uow, principal, AuditAction.USER_CREATED, user.id, changes, now=now)
        await remember_creation(
            uow, principal, idempotency, AuditResourceType.USER, user.id, now=now
        )
        await uow.commit()
    return Created(user)


async def list_users(
    principal: Principal, *, after: Keyset | None, limit: int, unit_of_work: UnitOfWorkFactory
) -> Page[User]:
    principal.require(Permission.USERS_MANAGE)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        users = await uow.users.page(after=None if after is None else after.id, limit=limit + 1)
    return page_of(users, limit, lambda user: Keyset(user.id))


async def get_user(principal: Principal, user_id: UUID, *, unit_of_work: UnitOfWorkFactory) -> User:
    """A user of the caller's tenant. Another tenant's user does not exist here (ADR-0009)."""
    principal.require(Permission.USERS_MANAGE)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        user = await uow.users.get(user_id)
    if user is None:
        raise NotFoundError("no such user")
    return user


@dataclass(frozen=True, slots=True)
class UserChanges:
    """Fields left as ``None`` keep their value."""

    full_name: str | None = None
    role: Role | None = None
    is_active: bool | None = None


async def change_user(
    principal: Principal,
    user_id: UUID,
    changes: UserChanges,
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> User:
    """Rename, change the role of, or (de)activate a user; the tenant keeps an active admin."""
    principal.require(Permission.USERS_MANAGE)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        user = await uow.users.get(user_id)
        if user is None:
            raise NotFoundError("no such user")
        if user.version != expected_version:
            raise StaleVersionError("the user was changed by someone else; reload it")
        before = user_fields(user)
        was_active_admin = user.is_active_admin
        user.change(full_name=changes.full_name, role=changes.role, is_active=changes.is_active)
        # The admins stay locked until commit: a concurrent demotion of the other admin waits for
        # this one and then counts again.
        if (
            was_active_admin
            and not user.is_active_admin
            and await uow.users.lock_active_admins() == [user.id]
        ):
            raise LastAdminError("the tenant needs at least one active admin")
        await uow.users.save(user)
        edits = changed(before, user_fields(user))
        await record(uow, principal, AuditAction.USER_UPDATED, user.id, edits, now=clock())
        await uow.commit()
    return user


async def unlock_user(
    principal: Principal, user_id: UUID, *, unit_of_work: UnitOfWorkFactory, clock: Clock
) -> User:
    """Let a user locked out by failed sign-ins try again."""
    principal.require(Permission.USERS_MANAGE)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        user = await uow.users.get(user_id)
        if user is None:
            raise NotFoundError("no such user")
        before = user_fields(user)
        user.unlock()
        await uow.users.save_login_state(user)
        changes = changed(before, user_fields(user))
        await record(uow, principal, AuditAction.USER_UNLOCKED, user.id, changes, now=clock())
        await uow.commit()
    return user
