"""In-memory fakes of the ports (handbook §7: don't mock what you own, write a fake).

They follow the same rules as the SQLAlchemy adapters, whose integration tests pin those rules down:
tenant scoping, explicit commits and unique emails.
"""

import copy
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID

from pricewright.api.dependencies import Services
from pricewright.application.ports import (
    ApiKeyRepository,
    IdentityLookup,
    IssuedToken,
    RefreshTokenRepository,
    ServiceAccountRepository,
    TenantRepository,
    UnitOfWork,
    UserRepository,
)
from pricewright.domain.auth import AuthenticationError, Principal
from pricewright.domain.errors import ConflictError, StaleVersionError
from pricewright.domain.service_accounts import ApiKey, ServiceAccount
from pricewright.domain.sessions import RefreshToken
from pricewright.domain.tenants import Tenant
from pricewright.domain.users import Role, User


@dataclass
class InMemoryDatabase:
    tenants: dict[UUID, Tenant] = field(default_factory=dict)
    users: dict[UUID, User] = field(default_factory=dict)
    refresh_tokens: dict[UUID, RefreshToken] = field(default_factory=dict)
    service_accounts: dict[UUID, ServiceAccount] = field(default_factory=dict)
    api_keys: dict[UUID, ApiKey] = field(default_factory=dict)


class FakeTenantRepository:
    def __init__(self, tenants: dict[UUID, Tenant]) -> None:
        self._tenants = tenants

    async def add(self, tenant: Tenant) -> None:
        self._tenants[tenant.id] = tenant

    async def get(self, tenant_id: UUID) -> Tenant | None:
        return copy.deepcopy(self._tenants.get(tenant_id))

    async def save(self, tenant: Tenant) -> None:
        stored = self._tenants.get(tenant.id)
        if stored is None or stored.version != tenant.version:
            raise StaleVersionError("the tenant was changed by someone else; reload it")
        tenant.version += 1
        self._tenants[tenant.id] = copy.deepcopy(tenant)


class FakeUserRepository:
    def __init__(self, users: dict[UUID, User], uow: FakeUnitOfWork) -> None:
        self._users = users
        self._uow = uow

    async def add(self, user: User) -> None:
        if user.tenant_id != self._uow.tenant_id:
            raise RuntimeError("a user can only be added to the unit of work's tenant")
        self._users[user.id] = user

    async def get(self, user_id: UUID) -> User | None:
        user = self._users.get(user_id)
        if user is None or user.tenant_id != self._uow.tenant_id:
            return None
        return copy.deepcopy(user)  # like the adapter: changes need save()

    async def page(self, *, after: UUID | None, limit: int) -> list[User]:
        owned = sorted(
            (user for user in self._users.values() if user.tenant_id == self._uow.tenant_id),
            key=lambda user: user.id,
        )
        return copy.deepcopy([user for user in owned if after is None or user.id > after][:limit])

    def _stored(self, user: User) -> User:
        if user.tenant_id != self._uow.tenant_id:
            raise RuntimeError("only a user of the unit of work's tenant can be saved")
        stored = self._users.get(user.id)
        if stored is None:
            raise RuntimeError("only an existing user can be saved")
        return stored

    async def save(self, user: User) -> None:
        stored = self._stored(user)
        if stored.version != user.version:
            raise StaleVersionError("the user was changed by someone else; reload it")
        stored.full_name, stored.role, stored.is_active = user.full_name, user.role, user.is_active
        stored.version += 1
        user.version = stored.version

    async def save_login_state(self, user: User) -> None:
        stored = self._stored(user)
        if stored.is_locked != user.is_locked:  # like the adapter: only `locked` is visible
            stored.version += 1
        stored.failed_login_attempts = user.failed_login_attempts
        stored.password_hash = user.password_hash
        user.version = stored.version

    async def lock_active_admins(self) -> list[UUID]:
        users = self._users.values()
        tenant_id = self._uow.tenant_id
        return sorted(u.id for u in users if u.tenant_id == tenant_id and u.is_active_admin)


class FakeRefreshTokenRepository:
    def __init__(self, tokens: dict[UUID, RefreshToken], uow: FakeUnitOfWork) -> None:
        self._tokens = tokens
        self._uow = uow

    def _owned(self) -> list[RefreshToken]:
        return [token for token in self._tokens.values() if token.tenant_id == self._uow.tenant_id]

    async def add(self, token: RefreshToken) -> None:
        if token.tenant_id != self._uow.tenant_id:
            raise RuntimeError("a refresh token can only be added to the unit of work's tenant")
        self._tokens[token.id] = token

    async def claim(self, token_id: UUID, now: datetime) -> bool:
        token = next((token for token in self._owned() if token.id == token_id), None)
        if token is None or token.used_at is not None or token.revoked_at is not None:
            return False
        token.used_at = now
        return True

    async def revoke_family(self, family_id: UUID, now: datetime) -> None:
        for token in self._owned():
            if token.family_id == family_id and token.revoked_at is None:
                token.revoked_at = now


class FakeServiceAccountRepository:
    def __init__(self, accounts: dict[UUID, ServiceAccount], uow: FakeUnitOfWork) -> None:
        self._accounts = accounts
        self._uow = uow

    async def add(self, account: ServiceAccount) -> None:
        if account.tenant_id != self._uow.tenant_id:
            raise RuntimeError("a service account can only be added to the unit of work's tenant")
        self._accounts[account.id] = account

    async def get(self, account_id: UUID) -> ServiceAccount | None:
        account = self._accounts.get(account_id)
        if account is None or account.tenant_id != self._uow.tenant_id:
            return None
        return copy.deepcopy(account)

    async def page(self, *, after: UUID | None, limit: int) -> list[ServiceAccount]:
        owned = sorted(
            (a for a in self._accounts.values() if a.tenant_id == self._uow.tenant_id),
            key=lambda account: account.id,
        )
        return copy.deepcopy([a for a in owned if after is None or a.id > after][:limit])


class FakeApiKeyRepository:
    def __init__(self, keys: dict[UUID, ApiKey], uow: FakeUnitOfWork) -> None:
        self._keys = keys
        self._uow = uow

    async def add(self, key: ApiKey) -> None:
        if key.tenant_id != self._uow.tenant_id:
            raise RuntimeError("an API key can only be added to the unit of work's tenant")
        self._keys[key.id] = key

    async def get(self, key_id: UUID) -> ApiKey | None:
        key = self._keys.get(key_id)
        return None if key is None or key.tenant_id != self._uow.tenant_id else copy.deepcopy(key)

    async def active_for(self, account_id: UUID) -> list[ApiKey]:
        keys = (
            key
            for key in self._keys.values()
            if key.tenant_id == self._uow.tenant_id
            and key.service_account_id == account_id
            and key.revoked_at is None
        )
        return copy.deepcopy(sorted(keys, key=lambda key: key.id))

    async def save(self, key: ApiKey) -> None:
        stored = self._keys.get(key.id)
        if stored is not None and stored.tenant_id == self._uow.tenant_id:
            stored.last_used_at, stored.revoked_at = key.last_used_at, key.revoked_at


class FakeIdentityLookup:
    def __init__(self, database: InMemoryDatabase) -> None:
        self._database = database

    async def user_by_email(self, email: str) -> User | None:
        users = self._database.users.values()
        return copy.deepcopy(next((user for user in users if user.email == email), None))

    async def refresh_token_by_digest(self, token_digest: str) -> RefreshToken | None:
        tokens = self._database.refresh_tokens.values()
        found = next((token for token in tokens if token.token_digest == token_digest), None)
        return copy.deepcopy(found)

    async def api_key_by_digest(self, key_digest: str) -> ApiKey | None:
        keys = self._database.api_keys.values()
        return copy.deepcopy(next((key for key in keys if key.key_digest == key_digest), None))


class FakeUnitOfWork:
    """Works on a copy of the database; ``commit`` writes the copy back."""

    tenants: TenantRepository
    users: UserRepository
    refresh_tokens: RefreshTokenRepository
    service_accounts: ServiceAccountRepository
    api_keys: ApiKeyRepository
    identities: IdentityLookup

    def __init__(self, database: InMemoryDatabase) -> None:
        self._database = database
        self._tenant_id: UUID | None = None

    async def __aenter__(self) -> Self:
        self._staged = copy.deepcopy(self._database)
        self.tenants = FakeTenantRepository(self._staged.tenants)
        self.users = FakeUserRepository(self._staged.users, self)
        self.refresh_tokens = FakeRefreshTokenRepository(self._staged.refresh_tokens, self)
        self.service_accounts = FakeServiceAccountRepository(self._staged.service_accounts, self)
        self.api_keys = FakeApiKeyRepository(self._staged.api_keys, self)
        self.identities = FakeIdentityLookup(self._staged)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del self._staged

    @property
    def tenant_id(self) -> UUID:
        if self._tenant_id is None:
            raise RuntimeError("tenant-owned data accessed before the unit of work was bound")
        return self._tenant_id

    def bind_tenant(self, tenant_id: UUID) -> None:
        if self._tenant_id is not None and self._tenant_id != tenant_id:
            raise RuntimeError("the unit of work is already bound to another tenant")
        self._tenant_id = tenant_id

    async def commit(self) -> None:
        emails = [user.email for user in self._staged.users.values()]
        if len(emails) != len(set(emails)):
            raise ConflictError("the change conflicts with an existing record")
        self._database.tenants = copy.deepcopy(self._staged.tenants)
        self._database.users = copy.deepcopy(self._staged.users)
        self._database.refresh_tokens = copy.deepcopy(self._staged.refresh_tokens)
        self._database.service_accounts = copy.deepcopy(self._staged.service_accounts)
        self._database.api_keys = copy.deepcopy(self._staged.api_keys)


class FakePasswordHasher:
    """Readable and instant: ``hash("secret")`` is ``"hashed:secret"``.

    Hashes without the prefix count as made with old parameters (``needs_rehash``), and every
    verification is recorded, so tests can check that unknown users cost the same work.
    """

    PREFIX = "hashed:"

    def __init__(self) -> None:
        self.verified: list[str] = []

    async def hash(self, password: str) -> str:
        return self.PREFIX + password

    async def verify(self, password_hash: str, password: str) -> bool:
        self.verified.append(password)
        return password_hash in {self.PREFIX + password, "legacy:" + password}

    async def verify_unknown(self, password: str) -> None:
        self.verified.append(password)

    def needs_rehash(self, password_hash: str) -> bool:
        return not password_hash.startswith(self.PREFIX)


class FakeClock:
    """A clock that only moves when the test says so."""

    def __init__(self, now: datetime = datetime(2026, 10, 3, 12, tzinfo=UTC)) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class FakeAccessTokens:
    """Tokens are ``token:<tenant>:<user>:<role>``; anything else is rejected."""

    EXPIRES_IN = 900

    def issue(self, principal: Principal) -> IssuedToken:
        token = f"token:{principal.tenant_id}:{principal.subject_id}:{principal.role}"
        return IssuedToken(token=token, expires_in=self.EXPIRES_IN)

    def read(self, token: str) -> Principal:
        try:
            prefix, tenant_id, user_id, role = token.split(":")
            if prefix != "token":
                raise ValueError(prefix)
            return Principal(uuid.UUID(tenant_id), uuid.UUID(user_id), Role(role))
        except ValueError as error:
            raise AuthenticationError("invalid access token") from error


def fake_services(
    database: InMemoryDatabase | None = None, clock: FakeClock | None = None
) -> Services:
    """API services backed by the fakes: an app that runs without infrastructure."""
    shared = database if database is not None else InMemoryDatabase()

    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(shared)

    return Services(
        unit_of_work=unit_of_work,
        hasher=FakePasswordHasher(),
        access_tokens=FakeAccessTokens(),
        clock=clock if clock is not None else FakeClock(),
    )
