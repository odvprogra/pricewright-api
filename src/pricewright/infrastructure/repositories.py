"""SQLAlchemy implementations of the repository ports (ADR-0011)."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.infrastructure.records import TenantRecord


class SqlAlchemyTenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, tenant: Tenant) -> None:
        self._session.add(
            TenantRecord(
                id=tenant.id,
                name=tenant.name,
                currency=tenant.settings.currency,
                tax_rate=tenant.settings.tax_rate,
                approval_threshold=tenant.settings.approval_threshold,
            )
        )

    async def get(self, tenant_id: UUID) -> Tenant | None:
        record = await self._session.get(TenantRecord, tenant_id)
        if record is None:
            return None
        return Tenant(
            id=record.id,
            name=record.name,
            settings=TenantSettings(
                currency=record.currency,
                tax_rate=record.tax_rate,
                approval_threshold=record.approval_threshold,
            ),
        )
