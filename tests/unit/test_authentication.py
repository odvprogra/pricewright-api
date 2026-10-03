import uuid
from decimal import Decimal

import pytest

from pricewright.application.authentication import (
    INVALID_CREDENTIALS,
    Credentials,
    log_in,
)
from pricewright.application.ports import IssuedToken, UnitOfWork
from pricewright.domain.auth import AuthenticationError, Principal
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import MAX_FAILED_LOGINS, Role, User
from tests.fakes import FakeAccessTokens, FakePasswordHasher, FakeUnitOfWork, InMemoryDatabase

PASSWORD = "avery's long passphrase"


class Fixture:
    """A tenant with one sales manager, Avery, and the fakes ``log_in`` needs."""

    def __init__(self, password_hash: str = FakePasswordHasher.PREFIX + PASSWORD) -> None:
        tenant = Tenant.register(name="Northfield", settings=TenantSettings("USD", Decimal(0)))
        self.avery = User.create(
            tenant_id=tenant.id,
            email="avery@northfield.example",
            full_name="Avery",
            role=Role.SALES_MANAGER,
            password_hash=password_hash,
        )
        self.database = InMemoryDatabase(
            tenants={tenant.id: tenant}, users={self.avery.id: self.avery}
        )
        self.hasher = FakePasswordHasher()
        self.tokens = FakeAccessTokens()

    def stored_avery(self) -> User:
        return self.database.users[self.avery.id]

    async def log_in(
        self, email: str = "avery@northfield.example", password: str = PASSWORD
    ) -> IssuedToken:
        def unit_of_work() -> UnitOfWork:
            return FakeUnitOfWork(self.database)

        return await log_in(
            Credentials(email=email, password=password),
            unit_of_work=unit_of_work,
            hasher=self.hasher,
            access_tokens=self.tokens,
        )


async def test_log_in_issues_a_token_for_the_users_tenant_and_role() -> None:
    fixture = Fixture()

    issued = await fixture.log_in(email="  AVERY@northfield.example")

    principal = fixture.tokens.read(issued.token)
    assert principal == Principal(fixture.avery.tenant_id, fixture.avery.id, Role.SALES_MANAGER)
    assert issued.expires_in == FakeAccessTokens.EXPIRES_IN


async def test_log_in_with_a_wrong_password_counts_a_failed_attempt() -> None:
    fixture = Fixture()

    with pytest.raises(AuthenticationError, match=INVALID_CREDENTIALS):
        await fixture.log_in(password="not avery's passphrase")

    assert fixture.stored_avery().failed_login_attempts == 1


async def test_log_in_success_resets_the_failed_attempts() -> None:
    fixture = Fixture()
    fixture.stored_avery().failed_login_attempts = 3

    await fixture.log_in()

    assert fixture.stored_avery().failed_login_attempts == 0


@pytest.mark.parametrize("email", ["nobody@northfield.example", "not an email"])
async def test_log_in_with_an_unknown_email_fails_after_the_same_work(email: str) -> None:
    fixture = Fixture()

    with pytest.raises(AuthenticationError, match=INVALID_CREDENTIALS):
        await fixture.log_in(email=email)

    assert len(fixture.hasher.verified) == 1


async def test_log_in_to_a_locked_account_fails_even_with_the_right_password() -> None:
    fixture = Fixture()
    fixture.stored_avery().failed_login_attempts = MAX_FAILED_LOGINS

    with pytest.raises(AuthenticationError, match=INVALID_CREDENTIALS):
        await fixture.log_in()

    assert fixture.stored_avery().failed_login_attempts == MAX_FAILED_LOGINS
    assert len(fixture.hasher.verified) == 1


async def test_log_in_to_a_deactivated_account_fails() -> None:
    fixture = Fixture()
    fixture.stored_avery().is_active = False

    with pytest.raises(AuthenticationError, match=INVALID_CREDENTIALS):
        await fixture.log_in()


async def test_log_in_rehashes_a_password_hashed_with_old_parameters() -> None:
    fixture = Fixture(password_hash="legacy:" + PASSWORD)

    await fixture.log_in()

    assert fixture.stored_avery().password_hash == FakePasswordHasher.PREFIX + PASSWORD


async def test_log_in_treats_equivalent_unicode_passwords_as_equal() -> None:
    fixture = Fixture(password_hash=FakePasswordHasher.PREFIX + "contraseña segura 2026")

    issued = await fixture.log_in(password="contraseña segura 2026")

    assert fixture.tokens.read(issued.token).user_id == fixture.avery.id


def test_credentials_never_show_the_password() -> None:
    assert PASSWORD not in repr(Credentials(email="avery@northfield.example", password=PASSWORD))


def test_fake_access_tokens_reject_anything_they_did_not_issue() -> None:
    with pytest.raises(AuthenticationError):
        FakeAccessTokens().read(f"forged:{uuid.uuid7()}:{uuid.uuid7()}:admin")
