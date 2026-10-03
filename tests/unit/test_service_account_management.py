import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from pricewright.application.ports import UnitOfWork
from pricewright.application.service_accounts import (
    create_service_account,
    get_service_account,
    issue_api_key,
    list_api_keys,
    list_service_accounts,
    revoke_api_key,
)
from pricewright.domain.auth import Permission, PermissionDeniedError, Principal
from pricewright.domain.digests import digest
from pricewright.domain.errors import NotFoundError
from pricewright.domain.service_accounts import (
    InvalidServiceAccountError,
    ServiceAccount,
    TooManyApiKeysError,
    is_well_formed,
)
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeClock, FakeUnitOfWork, InMemoryDatabase

READ_TENANT = frozenset({Permission.TENANT_READ})


class Fixture:
    def __init__(self) -> None:
        settings = TenantSettings("USD", Decimal(0))
        self.northfield = Tenant.register(name="Northfield", settings=settings)
        self.larkspur = Tenant.register(name="Larkspur", settings=settings)
        self.database = InMemoryDatabase(
            tenants={self.northfield.id: self.northfield, self.larkspur.id: self.larkspur}
        )
        self.clock = FakeClock()

    def unit_of_work(self) -> UnitOfWork:
        return FakeUnitOfWork(self.database)

    def admin(self, tenant: Tenant | None = None) -> Principal:
        return Principal((tenant or self.northfield).id, uuid.uuid7(), Role.ADMIN)

    async def account(self, name: str = "ops-copilot") -> ServiceAccount:
        return await create_service_account(
            self.admin(), name=name, scopes=READ_TENANT, unit_of_work=self.unit_of_work
        )


async def test_an_admin_creates_and_lists_service_accounts() -> None:
    fixture = Fixture()
    mcp = await fixture.account("erp-mcp-server")
    copilot = await fixture.account("ops-copilot")

    page = await list_service_accounts(
        fixture.admin(), after=None, limit=1, unit_of_work=fixture.unit_of_work
    )
    rest = await list_service_accounts(
        fixture.admin(), after=page.next_after, limit=1, unit_of_work=fixture.unit_of_work
    )

    assert (page.items, rest.items, rest.next_after) == ([mcp], [copilot], None)


async def test_service_accounts_cannot_get_administrative_scopes() -> None:
    fixture = Fixture()

    with pytest.raises(InvalidServiceAccountError):
        await create_service_account(
            fixture.admin(),
            name="sneaky",
            scopes=frozenset({Permission.USERS_MANAGE}),
            unit_of_work=fixture.unit_of_work,
        )


async def test_issued_keys_are_returned_once_and_stored_as_digests() -> None:
    fixture = Fixture()
    account = await fixture.account()

    issued = await issue_api_key(
        fixture.admin(),
        account.id,
        expires_at=None,
        unit_of_work=fixture.unit_of_work,
        clock=fixture.clock,
    )

    assert is_well_formed(issued.secret)
    assert issued.secret not in repr(issued)
    [stored] = fixture.database.api_keys.values()
    assert stored.key_digest == digest(issued.secret)
    assert stored.created_at == fixture.clock()


async def test_an_account_has_at_most_two_active_keys() -> None:
    fixture = Fixture()
    account = await fixture.account()

    async def issue() -> uuid.UUID:
        issued = await issue_api_key(
            fixture.admin(),
            account.id,
            expires_at=fixture.clock() + timedelta(days=30),
            unit_of_work=fixture.unit_of_work,
            clock=fixture.clock,
        )
        return issued.key.id

    first, _ = await issue(), await issue()
    with pytest.raises(TooManyApiKeysError):
        await issue()

    await revoke_api_key(
        fixture.admin(), account.id, first, unit_of_work=fixture.unit_of_work, clock=fixture.clock
    )
    await issue()  # revoking one makes room
    assert (
        len(await list_api_keys(fixture.admin(), account.id, unit_of_work=fixture.unit_of_work))
        == 2
    )


async def test_another_tenant_cannot_see_or_use_an_account() -> None:
    fixture = Fixture()
    account = await fixture.account()
    outsider = fixture.admin(fixture.larkspur)

    with pytest.raises(NotFoundError):
        await get_service_account(outsider, account.id, unit_of_work=fixture.unit_of_work)
    with pytest.raises(NotFoundError):
        await issue_api_key(
            outsider,
            account.id,
            expires_at=None,
            unit_of_work=fixture.unit_of_work,
            clock=fixture.clock,
        )
    with pytest.raises(NotFoundError):
        await list_api_keys(outsider, account.id, unit_of_work=fixture.unit_of_work)


async def test_revoking_a_key_of_another_account_is_not_found() -> None:
    fixture = Fixture()
    mcp, copilot = await fixture.account("erp-mcp-server"), await fixture.account("ops-copilot")
    issued = await issue_api_key(
        fixture.admin(),
        mcp.id,
        expires_at=None,
        unit_of_work=fixture.unit_of_work,
        clock=fixture.clock,
    )

    with pytest.raises(NotFoundError):
        await revoke_api_key(
            fixture.admin(),
            copilot.id,
            issued.key.id,
            unit_of_work=fixture.unit_of_work,
            clock=fixture.clock,
        )


@pytest.mark.parametrize("role", [Role.SALES_REP, Role.SALES_MANAGER])
async def test_only_admins_manage_service_accounts(role: Role) -> None:
    fixture = Fixture()

    with pytest.raises(PermissionDeniedError):
        await create_service_account(
            Principal(fixture.northfield.id, uuid.uuid7(), role),
            name="ops-copilot",
            scopes=READ_TENANT,
            unit_of_work=fixture.unit_of_work,
        )
