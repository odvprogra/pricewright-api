"""SQLAlchemy unit of work: one session and one transaction per business operation (ADR-0011)."""

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pricewright.application.ports import TenantRepository
from pricewright.infrastructure.repositories import SqlAlchemyTenantRepository


class SqlAlchemyUnitOfWork:
    """Opens a session on enter; anything not committed is rolled back on exit."""

    tenants: TenantRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        self._session = self._session_factory()
        self.tenants = SqlAlchemyTenantRepository(self._session)
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

    async def commit(self) -> None:
        await self._active_session().commit()

    def _active_session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("the unit of work is used outside `async with`")
        return self._session
