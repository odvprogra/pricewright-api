"""In-memory fakes of the ports (handbook §7: don't mock what you own, write a fake).

They follow the same rules as the SQLAlchemy adapters, whose integration tests pin those rules down:
tenant scoping, explicit commits and unique emails.
"""

import copy
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self
from uuid import UUID

from pricewright.application.ports import IdentityLookup, TenantRepository, UserRepository
from pricewright.domain.errors import ConflictError
from pricewright.domain.tenants import Tenant
from pricewright.domain.users import User


@dataclass
class InMemoryDatabase:
    tenants: dict[UUID, Tenant] = field(default_factory=dict)
    users: dict[UUID, User] = field(default_factory=dict)


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
        return user if user is not None and user.tenant_id == self._uow.tenant_id else None


class FakeIdentityLookup:
    def __init__(self, users: dict[UUID, User]) -> None:
        self._users = users

    async def user_by_email(self, email: str) -> User | None:
        return next((user for user in self._users.values() if user.email == email), None)


class FakeUnitOfWork:
    """Works on a copy of the database; ``commit`` writes the copy back."""

    tenants: TenantRepository
    users: UserRepository
    identities: IdentityLookup

    def __init__(self, database: InMemoryDatabase) -> None:
        self._database = database
        self._tenant_id: UUID | None = None

    async def __aenter__(self) -> Self:
        self._staged = copy.deepcopy(self._database)
        self.tenants = FakeTenantRepository(self._staged.tenants)
        self.users = FakeUserRepository(self._staged.users, self)
        self.identities = FakeIdentityLookup(self._staged.users)
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


class FakePasswordHasher:
    """Readable and instant: ``hash("secret")`` is ``"hashed:secret"``."""

    PREFIX = "hashed:"

    async def hash(self, password: str) -> str:
        return self.PREFIX + password

    async def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == self.PREFIX + password

    def needs_rehash(self, password_hash: str) -> bool:
        return not password_hash.startswith(self.PREFIX)
