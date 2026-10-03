import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from pricewright.application.authentication import (
    INVALID_CREDENTIALS,
    INVALID_REFRESH,
    Credentials,
    TokenPair,
    log_in,
    log_out,
    refresh_session,
)
from pricewright.application.ports import UnitOfWork
from pricewright.domain.auth import AuthenticationError, Principal
from pricewright.domain.digests import digest
from pricewright.domain.sessions import ABSOLUTE_LIFETIME, IDLE_LIFETIME, RefreshToken
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import MAX_FAILED_LOGINS, Role, User
from tests.fakes import (
    FakeAccessTokens,
    FakeClock,
    FakePasswordHasher,
    FakeUnitOfWork,
    InMemoryDatabase,
)

PASSWORD = "avery's long passphrase"


class Fixture:
    """A tenant with one sales manager, Avery, and the fakes the use cases need."""

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
        self.clock = FakeClock()

    def unit_of_work(self) -> UnitOfWork:
        return FakeUnitOfWork(self.database)

    def stored_avery(self) -> User:
        return self.database.users[self.avery.id]

    def session(self, refresh_token: str) -> RefreshToken:
        return next(
            token
            for token in self.database.refresh_tokens.values()
            if token.token_digest == digest(refresh_token)
        )

    async def log_in(
        self, email: str = "avery@northfield.example", password: str = PASSWORD
    ) -> TokenPair:
        return await log_in(
            Credentials(email=email, password=password),
            unit_of_work=self.unit_of_work,
            hasher=self.hasher,
            access_tokens=self.tokens,
            clock=self.clock,
        )

    async def refresh(self, refresh_token: str) -> TokenPair:
        return await refresh_session(
            refresh_token,
            unit_of_work=self.unit_of_work,
            access_tokens=self.tokens,
            clock=self.clock,
        )


# --- log_in ------------------------------------------------------------------------------------


async def test_log_in_issues_tokens_for_the_users_tenant_and_role() -> None:
    fixture = Fixture()

    pair = await fixture.log_in(email="  AVERY@northfield.example")

    principal = fixture.tokens.read(pair.access.token)
    assert principal == Principal(fixture.avery.tenant_id, fixture.avery.id, Role.SALES_MANAGER)
    assert pair.access.expires_in == FakeAccessTokens.EXPIRES_IN
    session = fixture.session(pair.refresh_token)
    assert (session.user_id, session.expires_at) == (
        fixture.avery.id,
        fixture.clock() + IDLE_LIFETIME,
    )


async def test_log_in_with_a_wrong_password_counts_a_failed_attempt() -> None:
    fixture = Fixture()

    with pytest.raises(AuthenticationError, match=INVALID_CREDENTIALS):
        await fixture.log_in(password="not avery's passphrase")

    assert fixture.stored_avery().failed_login_attempts == 1
    assert fixture.database.refresh_tokens == {}


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

    pair = await fixture.log_in(password="contraseña segura 2026")

    assert fixture.tokens.read(pair.access.token).subject_id == fixture.avery.id


def test_credentials_and_token_pairs_never_show_secrets() -> None:
    credentials = Credentials(email="avery@northfield.example", password=PASSWORD)
    pair = TokenPair(
        access=FakeAccessTokens().issue(Principal(uuid.uuid7(), uuid.uuid7(), Role.ADMIN)),
        refresh_token="pwr_secret",
    )

    assert PASSWORD not in repr(credentials)
    assert "pwr_secret" not in repr(pair)


def test_fake_access_tokens_reject_anything_they_did_not_issue() -> None:
    with pytest.raises(AuthenticationError):
        FakeAccessTokens().read(f"forged:{uuid.uuid7()}:{uuid.uuid7()}:admin")


# --- refresh_session ---------------------------------------------------------------------------


async def test_refresh_rotates_the_refresh_token_and_issues_a_new_access_token() -> None:
    fixture = Fixture()
    first = await fixture.log_in()
    fixture.clock.advance(timedelta(days=1))

    second = await fixture.refresh(first.refresh_token)

    assert second.refresh_token != first.refresh_token
    assert fixture.tokens.read(second.access.token).subject_id == fixture.avery.id
    assert fixture.session(first.refresh_token).used_at == fixture.clock()
    assert (
        fixture.session(second.refresh_token).family_id
        == fixture.session(first.refresh_token).family_id
    )


async def test_refresh_with_a_used_token_revokes_the_whole_family() -> None:
    fixture = Fixture()
    first = await fixture.log_in()
    second = await fixture.refresh(first.refresh_token)

    with pytest.raises(AuthenticationError, match=INVALID_REFRESH):
        await fixture.refresh(first.refresh_token)  # a leaked copy is replayed

    with pytest.raises(AuthenticationError):
        await fixture.refresh(second.refresh_token)  # the legitimate holder is signed out too
    assert fixture.session(second.refresh_token).revoked_at is not None


async def test_refresh_after_the_idle_lifetime_fails() -> None:
    fixture = Fixture()
    first = await fixture.log_in()
    fixture.clock.advance(IDLE_LIFETIME)

    with pytest.raises(AuthenticationError, match=INVALID_REFRESH):
        await fixture.refresh(first.refresh_token)

    assert fixture.session(first.refresh_token).used_at is None  # nothing was saved


async def test_refresh_never_extends_a_session_past_its_absolute_lifetime() -> None:
    fixture = Fixture()
    started = fixture.clock()
    pair = await fixture.log_in()
    for _ in range(3):  # refreshed every 9 days: never idle
        fixture.clock.advance(timedelta(days=9))
        pair = await fixture.refresh(pair.refresh_token)

    assert fixture.session(pair.refresh_token).expires_at == started + ABSOLUTE_LIFETIME
    fixture.clock.advance(timedelta(days=3))
    with pytest.raises(AuthenticationError):
        await fixture.refresh(pair.refresh_token)


async def test_refresh_for_a_deactivated_user_fails_and_ends_the_session() -> None:
    fixture = Fixture()
    first = await fixture.log_in()
    fixture.stored_avery().is_active = False

    with pytest.raises(AuthenticationError, match=INVALID_REFRESH):
        await fixture.refresh(first.refresh_token)

    assert fixture.session(first.refresh_token).revoked_at is not None


async def test_refresh_with_an_unknown_token_fails() -> None:
    with pytest.raises(AuthenticationError, match=INVALID_REFRESH):
        await Fixture().refresh("pwr_never_issued")


# --- log_out -----------------------------------------------------------------------------------


async def test_log_out_revokes_the_session() -> None:
    fixture = Fixture()
    first = await fixture.log_in()

    await log_out(first.refresh_token, unit_of_work=fixture.unit_of_work, clock=fixture.clock)

    with pytest.raises(AuthenticationError):
        await fixture.refresh(first.refresh_token)


async def test_log_out_with_an_unknown_token_does_nothing() -> None:
    fixture = Fixture()

    await log_out("pwr_never_issued", unit_of_work=fixture.unit_of_work, clock=fixture.clock)

    assert fixture.database.refresh_tokens == {}
