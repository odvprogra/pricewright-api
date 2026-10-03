"""Managing service accounts and their API keys (admins: ``service_accounts:manage``)."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from pricewright.application.pagination import Page
from pricewright.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.errors import NotFoundError
from pricewright.domain.service_accounts import (
    MAX_ACTIVE_KEYS,
    ApiKey,
    ServiceAccount,
    TooManyApiKeysError,
    new_api_key,
)


@dataclass(frozen=True, slots=True)
class IssuedApiKey:
    key: ApiKey
    secret: str = field(repr=False)
    """The full key. It is never stored, so this is the only time anyone sees it."""


async def create_service_account(
    principal: Principal,
    *,
    name: str,
    scopes: frozenset[Permission],
    unit_of_work: UnitOfWorkFactory,
) -> ServiceAccount:
    principal.require(Permission.SERVICE_ACCOUNTS_MANAGE)
    account = ServiceAccount.create(tenant_id=principal.tenant_id, name=name, scopes=scopes)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        await uow.service_accounts.add(account)
        await uow.commit()  # a duplicate name in the tenant is a ConflictError
    return account


async def list_service_accounts(
    principal: Principal, *, after: UUID | None, limit: int, unit_of_work: UnitOfWorkFactory
) -> Page[ServiceAccount]:
    principal.require(Permission.SERVICE_ACCOUNTS_MANAGE)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        accounts = await uow.service_accounts.page(after=after, limit=limit + 1)
    page = accounts[:limit]
    return Page(items=page, next_after=page[-1].id if len(accounts) > limit else None)


async def get_service_account(
    principal: Principal, account_id: UUID, *, unit_of_work: UnitOfWorkFactory
) -> ServiceAccount:
    principal.require(Permission.SERVICE_ACCOUNTS_MANAGE)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        return await _account(uow, account_id)


async def issue_api_key(
    principal: Principal,
    account_id: UUID,
    *,
    expires_at: datetime | None,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> IssuedApiKey:
    """A new key for the account; at most ``MAX_ACTIVE_KEYS`` can be active at once."""
    principal.require(Permission.SERVICE_ACCOUNTS_MANAGE)
    secret = new_api_key()
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        # Locked until commit, so two concurrent requests cannot both pass the limit.
        account = await _account(uow, account_id, lock=True)
        if len(await uow.api_keys.active_for(account.id)) >= MAX_ACTIVE_KEYS:
            raise TooManyApiKeysError(
                f"a service account has at most {MAX_ACTIVE_KEYS} active keys; revoke one first"
            )
        key = ApiKey.issue(account, key=secret, now=clock(), expires_at=expires_at)
        await uow.api_keys.add(key)
        await uow.commit()
    return IssuedApiKey(key=key, secret=secret)


async def list_api_keys(
    principal: Principal, account_id: UUID, *, unit_of_work: UnitOfWorkFactory
) -> list[ApiKey]:
    """The account's active keys: never more than ``MAX_ACTIVE_KEYS``, so no paging."""
    principal.require(Permission.SERVICE_ACCOUNTS_MANAGE)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        account = await _account(uow, account_id)
        return await uow.api_keys.active_for(account.id)


async def revoke_api_key(
    principal: Principal,
    account_id: UUID,
    key_id: UUID,
    *,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> None:
    """Revoke a key for good. Revoking it again changes nothing."""
    principal.require(Permission.SERVICE_ACCOUNTS_MANAGE)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        key = await uow.api_keys.get(key_id)
        if key is None or key.service_account_id != account_id:
            raise NotFoundError("no such API key")
        key.revoke(clock())
        await uow.api_keys.save(key)
        await uow.commit()


async def _account(uow: UnitOfWork, account_id: UUID, *, lock: bool = False) -> ServiceAccount:
    account = await uow.service_accounts.get(account_id, lock=lock)
    if account is None:
        raise NotFoundError("no such service account")
    return account
