"""Ports: what use cases need from the outside world, as Protocols (ADR-0011).

Adapters in ``infrastructure`` implement them; tests use in-memory fakes.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from pricewright.domain.auth import Principal
from pricewright.domain.sessions import RefreshToken
from pricewright.domain.tenants import Tenant
from pricewright.domain.users import User


class TenantRepository(Protocol):
    async def add(self, tenant: Tenant) -> None: ...

    async def get(self, tenant_id: UUID) -> Tenant | None: ...

    async def save(self, tenant: Tenant) -> None:
        """Store changes and bump ``tenant.version``, atomically.

        Raise ``StaleVersionError`` if the stored version is no longer ``tenant.version``.
        """
        ...


class UserRepository(Protocol):
    """Users of the unit of work's tenant only (ADR-0006)."""

    async def add(self, user: User) -> None: ...

    async def get(self, user_id: UUID) -> User | None: ...

    async def list(self, *, after: UUID | None, limit: int) -> list[User]:
        """Up to ``limit`` users ordered by id, starting after ``after`` (keyset pagination)."""
        ...

    async def save(self, user: User) -> None:
        """Store changes to a user loaded from this repository."""
        ...


class RefreshTokenRepository(Protocol):
    """Refresh tokens of the unit of work's tenant only (ADR-0006)."""

    async def add(self, token: RefreshToken) -> None: ...

    async def claim(self, token_id: UUID, now: datetime) -> bool:
        """Mark the token used, atomically: False if it was already used or revoked.

        Two requests presenting the same token can never both succeed.
        """
        ...

    async def revoke_family(self, family_id: UUID, now: datetime) -> None: ...


class IdentityLookup(Protocol):
    """The only cross-tenant reads: finding who is signing in before their tenant is known."""

    async def user_by_email(self, email: str) -> User | None: ...

    async def refresh_token_by_digest(self, token_digest: str) -> RefreshToken | None: ...


class PasswordHasher(Protocol):
    """Slow, salted hashing (argon2id). Async: hashing is CPU-bound and must not block requests."""

    async def hash(self, password: str) -> str: ...

    async def verify(self, password_hash: str, password: str) -> bool: ...

    async def verify_unknown(self, password: str) -> None:
        """Spend the work of a verification when there is no user, so timing reveals nothing."""
        ...

    def needs_rehash(self, password_hash: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    expires_in: int
    """Seconds until the token expires."""


class AccessTokens(Protocol):
    """Short-lived, signed access tokens (ADR-0007)."""

    def issue(self, principal: Principal) -> IssuedToken: ...

    def read(self, token: str) -> Principal:
        """Return the token's principal; raise ``AuthenticationError`` if it is not valid."""
        ...


class UnitOfWork(Protocol):
    """One atomic business operation: changes are saved by ``commit`` or discarded on exit.

    Tenant-owned repositories (``users``, ``refresh_tokens``) work only after ``bind_tenant``, and
    a unit of work can never be bound to a second tenant (ADR-0006).
    """

    tenants: TenantRepository
    users: UserRepository
    refresh_tokens: RefreshTokenRepository
    identities: IdentityLookup

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def bind_tenant(self, tenant_id: UUID) -> None: ...

    async def commit(self) -> None: ...


type UnitOfWorkFactory = Callable[[], UnitOfWork]
"""Opens a fresh unit of work; use cases open one per business operation."""

type Clock = Callable[[], datetime]
"""The current time, timezone-aware UTC."""
