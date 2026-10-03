"""In-memory fakes of the ports (handbook §7: don't mock what you own, write a fake).

They follow the same rules as the SQLAlchemy adapters, whose integration tests pin those rules down:
tenant scoping, explicit commits and unique emails.
"""

import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Self
from uuid import UUID

from pricewright.api.dependencies import Services
from pricewright.application.ports import (
    IdentityLookup,
    IssuedToken,
    RefreshTokenRepository,
    TenantRepository,
    UnitOfWork,
    UserRepository,
)
from pricewright.domain.auth import AuthenticationError, Principal
from pricewright.domain.errors import ConflictError
from pricewright.domain.sessions import RefreshToken
from pricewright.domain.tenants import Tenant
from pricewright.domain.users import Role, User


@dataclass
class InMemoryDatabase:
    tenants: dict[UUID, Tenant] = field(default_factory=dict)
    users: dict[UUID, User] = field(default_factory=dict)
    refresh_tokens: dict[UUID, RefreshToken] = field(default_factory=dict)


class FakeTenantRepository:
    def __init__(self, tenants: dict[UUID, Tenant]) -> None:
        self._tenants = tenants

    async def add(self, tenant: Tenant) -> None:
        self._tenants[tenant.id] = tenant

    async def get(self, tenant_id: UUID) -> Tenant | None:
        return self._tenants.get(tenant_id)


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

    async def save(self, user: User) -> None:
        stored = self._users.get(user.id)
        if stored is None or stored.tenant_id != self._uow.tenant_id:
            raise RuntimeError("only an existing user of the unit of work's tenant can be saved")
        self._users[user.id] = copy.deepcopy(user)


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


class FakeUnitOfWork:
    """Works on a copy of the database; ``commit`` writes the copy back."""

    tenants: TenantRepository
    users: UserRepository
    refresh_tokens: RefreshTokenRepository
    identities: IdentityLookup

    def __init__(self, database: InMemoryDatabase) -> None:
        self._database = database
        self._tenant_id: UUID | None = None

    async def __aenter__(self) -> Self:
        self._staged = copy.deepcopy(self._database)
        self.tenants = FakeTenantRepository(self._staged.tenants)
        self.users = FakeUserRepository(self._staged.users, self)
        self.refresh_tokens = FakeRefreshTokenRepository(self._staged.refresh_tokens, self)
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


class FakeAccessTokens:
    """Tokens are ``token:<tenant>:<user>:<role>``; anything else is rejected."""

    EXPIRES_IN = 900

    def issue(self, principal: Principal) -> IssuedToken:
        token = f"token:{principal.tenant_id}:{principal.user_id}:{principal.role}"
        return IssuedToken(token=token, expires_in=self.EXPIRES_IN)

    def read(self, token: str) -> Principal:
        try:
            prefix, tenant_id, user_id, role = token.split(":")
            if prefix != "token":
                raise ValueError(prefix)
            return Principal(uuid.UUID(tenant_id), uuid.UUID(user_id), Role(role))
        except ValueError as error:
            raise AuthenticationError("invalid access token") from error


def fake_services(database: InMemoryDatabase | None = None) -> Services:
    """API services backed by the fakes: an app that runs without infrastructure."""
    shared = database if database is not None else InMemoryDatabase()

    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(shared)

    return Services(
        unit_of_work=unit_of_work,
        hasher=FakePasswordHasher(),
        access_tokens=FakeAccessTokens(),
    )
