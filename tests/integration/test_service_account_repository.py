"""Service accounts and API keys in PostgreSQL, scoped to their tenant (ADR-0006)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from pricewright.domain.auth import Permission
from pricewright.domain.errors import ConflictError
from pricewright.domain.service_accounts import ApiKey, ServiceAccount, new_api_key
from pricewright.domain.tenants import Tenant
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import Sessions, register

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


async def add_account(sessions: Sessions, tenant: Tenant, name: str) -> ServiceAccount:
    account = ServiceAccount.create(
        tenant_id=tenant.id, name=name, scopes=frozenset({Permission.TENANT_READ})
    )
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(tenant.id)
        await uow.service_accounts.add(account)
        await uow.commit()
    return account


async def add_key(sessions: Sessions, account: ServiceAccount) -> ApiKey:
    key = ApiKey.issue(account, key=new_api_key(), now=NOW)
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(account.tenant_id)
        await uow.api_keys.add(key)
        await uow.commit()
    return key


async def test_service_accounts_are_stored_and_listed_per_tenant(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    larkspur, _ = await register(session_factory, "Larkspur")
    mcp = await add_account(session_factory, northfield, "erp-mcp-server")
    copilot = await add_account(session_factory, northfield, "ops-copilot")
    await add_account(session_factory, larkspur, "erp-mcp-server")  # same name, other tenant

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        listed = await uow.service_accounts.page(after=None, limit=10)
        after_first = await uow.service_accounts.page(after=mcp.id, limit=10)
        found = await uow.service_accounts.get(mcp.id)
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(larkspur.id)
        hidden = await uow.service_accounts.get(mcp.id)

    assert listed == [mcp, copilot]
    assert after_first == [copilot]
    assert found == mcp
    assert hidden is None


async def test_service_account_names_are_unique_within_a_tenant(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    await add_account(session_factory, northfield, "ops-copilot")

    with pytest.raises(ConflictError):
        await add_account(session_factory, northfield, "ops-copilot")


async def test_api_keys_are_found_by_digest_and_listed_while_active(
    session_factory: Sessions,
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    account = await add_account(session_factory, northfield, "ops-copilot")
    old, new = await add_key(session_factory, account), await add_key(session_factory, account)
    old.revoke(NOW + timedelta(days=1))
    new.record_use(NOW + timedelta(days=1))

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        await uow.api_keys.save(old)
        await uow.api_keys.save(new)
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        by_digest = await uow.identities.api_key_by_digest(new.key_digest)
        uow.bind_tenant(northfield.id)
        active = await uow.api_keys.active_for(account.id)
        revoked = await uow.api_keys.get(old.id)
    assert by_digest == new
    assert active == [new]
    assert revoked == old


async def test_another_tenant_cannot_read_or_add_keys_for_an_account(
    session_factory: Sessions,
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    larkspur, _ = await register(session_factory, "Larkspur")
    account = await add_account(session_factory, northfield, "ops-copilot")
    key = await add_key(session_factory, account)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(larkspur.id)
        assert await uow.api_keys.get(key.id) is None
        assert await uow.api_keys.active_for(account.id) == []
        with pytest.raises(RuntimeError, match="unit of work's tenant"):
            await uow.api_keys.add(ApiKey.issue(account, key=new_api_key(), now=NOW))
        with pytest.raises(RuntimeError, match="unit of work's tenant"):
            await uow.service_accounts.add(account)


async def test_database_rejects_a_key_whose_account_belongs_to_another_tenant(
    session_factory: Sessions,
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    larkspur, _ = await register(session_factory, "Larkspur")
    account = await add_account(session_factory, northfield, "ops-copilot")
    crossed = ApiKey.issue(account, key=new_api_key(), now=NOW)
    crossed.tenant_id = larkspur.id  # the application never does this; the key refuses it anyway

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(larkspur.id)
        await uow.api_keys.add(crossed)

        with pytest.raises(
            IntegrityError, match="fk_api_keys_tenant_id_service_account_id_service_accounts"
        ):
            await uow.commit()
