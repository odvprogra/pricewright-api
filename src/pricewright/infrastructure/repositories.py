"""SQLAlchemy implementations of the repository ports (ADR-0011)."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role, User
from pricewright.infrastructure.records import TenantRecord, UserRecord


class TenantScope:
    """The one tenant a unit of work may touch. Bound once; tenant-owned queries need it."""

    def __init__(self) -> None:
        self._tenant_id: UUID | None = None

    def bind(self, tenant_id: UUID) -> None:
        if self._tenant_id is not None and self._tenant_id != tenant_id:
            raise RuntimeError("the unit of work is already bound to another tenant")
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> UUID:
        if self._tenant_id is None:
            raise RuntimeError("tenant-owned data accessed before the unit of work was bound")
        return self._tenant_id


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


def _to_user(record: UserRecord) -> User:
    return User(
        id=record.id,
        tenant_id=record.tenant_id,
        email=record.email,
        full_name=record.full_name,
        role=Role(record.role),
        password_hash=record.password_hash,
        is_active=record.is_active,
        failed_login_attempts=record.failed_login_attempts,
    )


class SqlAlchemyUserRepository:
    """Every query is filtered by the bound tenant; inserts must belong to it (ADR-0006)."""

    def __init__(self, session: AsyncSession, scope: TenantScope) -> None:
        self._session = session
        self._scope = scope

    async def add(self, user: User) -> None:
        if user.tenant_id != self._scope.tenant_id:
            raise RuntimeError("a user can only be added to the unit of work's tenant")
        self._session.add(
            UserRecord(
                id=user.id,
                tenant_id=user.tenant_id,
                email=user.email,
                full_name=user.full_name,
                role=user.role.value,
                password_hash=user.password_hash,
                is_active=user.is_active,
                failed_login_attempts=user.failed_login_attempts,
            )
        )

    async def get(self, user_id: UUID) -> User | None:
        record = await self._session.scalar(
            select(UserRecord).where(
                UserRecord.tenant_id == self._scope.tenant_id, UserRecord.id == user_id
            )
        )
        return None if record is None else _to_user(record)

    async def save(self, user: User) -> None:
        result = await self._session.execute(
            update(UserRecord)
            .where(UserRecord.tenant_id == self._scope.tenant_id, UserRecord.id == user.id)
            .values(
                email=user.email,
                full_name=user.full_name,
                role=user.role.value,
                password_hash=user.password_hash,
                is_active=user.is_active,
                failed_login_attempts=user.failed_login_attempts,
            )
        )
        if result.rowcount != 1:  # type: ignore[attr-defined]  # UPDATE returns a CursorResult
            raise RuntimeError("only an existing user of the unit of work's tenant can be saved")


class SqlAlchemyIdentityLookup:
    """Cross-tenant by design, and only for authentication (ADR-0006)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def user_by_email(self, email: str) -> User | None:
        record = await self._session.scalar(select(UserRecord).where(UserRecord.email == email))
        return None if record is None else _to_user(record)
