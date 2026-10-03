"""Ports: what use cases need from the outside world, as Protocols (ADR-0011).

Adapters in ``infrastructure`` implement them; tests use in-memory fakes.
"""

from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from pricewright.domain.tenants import Tenant


class TenantRepository(Protocol):
    async def add(self, tenant: Tenant) -> None: ...

    async def get(self, tenant_id: UUID) -> Tenant | None: ...


class UnitOfWork(Protocol):
    """One atomic business operation: changes are saved by ``commit`` or discarded on exit."""

    tenants: TenantRepository

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
