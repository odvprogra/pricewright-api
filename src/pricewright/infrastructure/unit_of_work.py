"""SQLAlchemy unit of work: one session and one transaction per business operation (ADR-0011)."""

from types import TracebackType
from typing import Self
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pricewright.application.ports import (
    ApiKeyRepository,
    AuditEventRepository,
    IdentityLookup,
    RefreshTokenRepository,
    ServiceAccountRepository,
    TenantRepository,
    UserRepository,
)
from pricewright.domain.errors import ConflictError
from pricewright.infrastructure.repositories import (
    SqlAlchemyApiKeyRepository,
    SqlAlchemyAuditEventRepository,
    SqlAlchemyIdentityLookup,
    SqlAlchemyRefreshTokenRepository,
    SqlAlchemyServiceAccountRepository,
    SqlAlchemyTenantRepository,
    SqlAlchemyUserRepository,
    TenantScope,
)

_UNIQUE_VIOLATION = "23505"  # PostgreSQL SQLSTATE


class SqlAlchemyUnitOfWork:
    """Opens a session on enter; anything not committed is rolled back on exit."""

    tenants: TenantRepository
    users: UserRepository
    refresh_tokens: RefreshTokenRepository
    service_accounts: ServiceAccountRepository
    api_keys: ApiKeyRepository
    audit_events: AuditEventRepository
    identities: IdentityLookup

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._scope = TenantScope()

    async def __aenter__(self) -> Self:
        self._session = self._session_factory()
        self.tenants = SqlAlchemyTenantRepository(self._session)
        self.users = SqlAlchemyUserRepository(self._session, self._scope)
        self.refresh_tokens = SqlAlchemyRefreshTokenRepository(self._session, self._scope)
        self.service_accounts = SqlAlchemyServiceAccountRepository(self._session, self._scope)
        self.api_keys = SqlAlchemyApiKeyRepository(self._session, self._scope)
        self.audit_events = SqlAlchemyAuditEventRepository(self._session, self._scope)
        self.identities = SqlAlchemyIdentityLookup(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        session = self._active_session()
        try:
            await session.rollback()  # a no-op after commit
        finally:
            await session.close()
            self._session = None

    def bind_tenant(self, tenant_id: UUID) -> None:
        self._scope.bind(tenant_id)

    async def commit(self) -> None:
        try:
            await self._active_session().commit()
        except IntegrityError as error:
            # Use cases check uniqueness first; this covers the race between check and insert.
            if getattr(error.orig, "sqlstate", None) == _UNIQUE_VIOLATION:
                raise ConflictError("the change conflicts with an existing record") from error
            raise

    def _active_session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("the unit of work is used outside `async with`")
        return self._session
