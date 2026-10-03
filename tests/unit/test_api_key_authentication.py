from datetime import timedelta
from decimal import Decimal

import pytest

from pricewright.application.authentication import (
    INVALID_API_KEY,
    authenticate_api_key,
    current_user,
)
from pricewright.application.ports import UnitOfWork
from pricewright.domain.auth import (
    AuthenticationError,
    Permission,
    PermissionDeniedError,
    Principal,
)
from pricewright.domain.service_accounts import (
    LAST_USED_PRECISION,
    ApiKey,
    ServiceAccount,
    new_api_key,
)
from pricewright.domain.tenants import Tenant, TenantSettings
from tests.fakes import FakeClock, FakeUnitOfWork, InMemoryDatabase


class Fixture:
    """Northfield's ops-copilot account with one key, ``secret``."""

    def __init__(self, *, expires_in: timedelta | None = None) -> None:
        self.clock = FakeClock()
        tenant = Tenant.register(name="Northfield", settings=TenantSettings("USD", Decimal(0)))
        self.account = ServiceAccount.create(
            tenant_id=tenant.id, name="ops-copilot", scopes=frozenset({Permission.TENANT_READ})
        )
        self.secret = new_api_key()
        expires_at = None if expires_in is None else self.clock() + expires_in
        self.key = ApiKey.issue(
            self.account, key=self.secret, now=self.clock(), expires_at=expires_at
        )
        self.database = InMemoryDatabase(
            tenants={tenant.id: tenant},
            service_accounts={self.account.id: self.account},
            api_keys={self.key.id: self.key},
        )

    def unit_of_work(self) -> UnitOfWork:
        return FakeUnitOfWork(self.database)

    def stored_key(self) -> ApiKey:
        return self.database.api_keys[self.key.id]

    async def authenticate(self, key: str | None = None) -> Principal:
        return await authenticate_api_key(
            key or self.secret, unit_of_work=self.unit_of_work, clock=self.clock
        )


async def test_a_valid_key_authenticates_its_service_account_with_its_scopes() -> None:
    fixture = Fixture()

    principal = await fixture.authenticate()

    assert principal == Principal(
        fixture.account.tenant_id, fixture.account.id, scopes=fixture.account.scopes
    )
    assert principal.is_service_account


async def test_use_is_recorded_at_most_once_an_hour() -> None:
    fixture = Fixture()

    await fixture.authenticate()
    first_use = fixture.stored_key().last_used_at
    fixture.clock.advance(LAST_USED_PRECISION - timedelta(minutes=1))
    await fixture.authenticate()

    assert first_use == fixture.stored_key().last_used_at
    fixture.clock.advance(timedelta(minutes=1))
    await fixture.authenticate()
    assert fixture.stored_key().last_used_at == fixture.clock()


async def test_a_malformed_key_fails_without_opening_a_unit_of_work() -> None:
    def no_database() -> UnitOfWork:
        raise AssertionError("a malformed key must not reach the database")

    with pytest.raises(AuthenticationError, match=INVALID_API_KEY):
        await authenticate_api_key(
            "pwk_not-a-real-key", unit_of_work=no_database, clock=FakeClock()
        )


async def test_a_well_formed_key_that_was_never_issued_fails() -> None:
    with pytest.raises(AuthenticationError, match=INVALID_API_KEY):
        await Fixture().authenticate(new_api_key())


async def test_a_revoked_key_fails() -> None:
    fixture = Fixture()
    fixture.stored_key().revoke(fixture.clock())

    with pytest.raises(AuthenticationError):
        await fixture.authenticate()


async def test_an_expired_key_fails() -> None:
    fixture = Fixture(expires_in=timedelta(days=1))
    fixture.clock.advance(timedelta(days=1))

    with pytest.raises(AuthenticationError):
        await fixture.authenticate()


async def test_a_key_of_a_deactivated_account_fails() -> None:
    fixture = Fixture()
    fixture.database.service_accounts[fixture.account.id].is_active = False

    with pytest.raises(AuthenticationError):
        await fixture.authenticate()


async def test_service_accounts_have_no_user_profile() -> None:
    fixture = Fixture()
    principal = await fixture.authenticate()

    with pytest.raises(PermissionDeniedError):
        await current_user(principal, unit_of_work=fixture.unit_of_work)
