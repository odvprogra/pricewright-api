"""SQLAlchemy implementations of the repository ports (ADR-0011)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import case, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pricewright.domain.errors import StaleVersionError
from pricewright.domain.sessions import RefreshToken
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import MAX_FAILED_LOGINS, Role, User
from pricewright.infrastructure.records import RefreshTokenRecord, TenantRecord, UserRecord


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
                version=tenant.version,
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
            version=record.version,
        )

    async def save(self, tenant: Tenant) -> None:
        # Compare-and-set in one statement: of two concurrent saves, the second matches no row.
        new_version = await self._session.scalar(
            update(TenantRecord)
            .where(TenantRecord.id == tenant.id, TenantRecord.version == tenant.version)
            .values(
                name=tenant.name,
                tax_rate=tenant.settings.tax_rate,
                approval_threshold=tenant.settings.approval_threshold,
                version=TenantRecord.version + 1,
            )
            .returning(TenantRecord.version)
        )
        if new_version is None:
            raise StaleVersionError("the tenant was changed by someone else; reload it")
        tenant.version = new_version


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
        version=record.version,
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
                version=user.version,
            )
        )

    async def get(self, user_id: UUID) -> User | None:
        record = await self._session.scalar(
            select(UserRecord).where(
                UserRecord.tenant_id == self._scope.tenant_id, UserRecord.id == user_id
            )
        )
        return None if record is None else _to_user(record)

    async def page(self, *, after: UUID | None, limit: int) -> list[User]:
        query = select(UserRecord).where(UserRecord.tenant_id == self._scope.tenant_id)
        if after is not None:
            query = query.where(UserRecord.id > after)
        records = await self._session.scalars(query.order_by(UserRecord.id).limit(limit))
        return [_to_user(record) for record in records]

    def _require_own(self, user: User) -> None:
        if user.tenant_id != self._scope.tenant_id:
            raise RuntimeError("only a user of the unit of work's tenant can be saved")

    async def save(self, user: User) -> None:
        self._require_own(user)
        # Compare-and-set in one statement: of two concurrent edits, the second matches no row.
        new_version = await self._session.scalar(
            update(UserRecord)
            .where(
                UserRecord.tenant_id == self._scope.tenant_id,
                UserRecord.id == user.id,
                UserRecord.version == user.version,
            )
            .values(
                full_name=user.full_name,
                role=user.role.value,
                is_active=user.is_active,
                version=UserRecord.version + 1,
            )
            .returning(UserRecord.version)
        )
        if new_version is None:
            raise StaleVersionError("the user was changed by someone else; reload it")
        user.version = new_version

    async def save_login_state(self, user: User) -> None:
        self._require_own(user)
        # Only the locked flag is visible to admins, so only a change of it is a new version:
        # signing in must not make an admin's pending edit stale.
        was_locked = UserRecord.failed_login_attempts >= MAX_FAILED_LOGINS
        lock_changed = case((was_locked != literal(user.is_locked), 1), else_=0)
        new_version = await self._session.scalar(
            update(UserRecord)
            .where(UserRecord.tenant_id == self._scope.tenant_id, UserRecord.id == user.id)
            .values(
                failed_login_attempts=user.failed_login_attempts,
                password_hash=user.password_hash,
                version=UserRecord.version + lock_changed,
            )
            .returning(UserRecord.version)
        )
        if new_version is None:
            raise RuntimeError("only an existing user can be saved")
        user.version = new_version

    async def lock_active_admins(self) -> list[UUID]:
        ids = await self._session.scalars(
            select(UserRecord.id)
            .where(
                UserRecord.tenant_id == self._scope.tenant_id,
                UserRecord.role == Role.ADMIN.value,
                UserRecord.is_active,
            )
            .order_by(UserRecord.id)  # a stable lock order avoids deadlocks
            .with_for_update()
        )
        return list(ids)


def _to_refresh_token(record: RefreshTokenRecord) -> RefreshToken:
    return RefreshToken(
        id=record.id,
        tenant_id=record.tenant_id,
        user_id=record.user_id,
        family_id=record.family_id,
        token_digest=record.token_digest,
        expires_at=record.expires_at,
        family_expires_at=record.family_expires_at,
        used_at=record.used_at,
        revoked_at=record.revoked_at,
    )


class SqlAlchemyRefreshTokenRepository:
    def __init__(self, session: AsyncSession, scope: TenantScope) -> None:
        self._session = session
        self._scope = scope

    async def add(self, token: RefreshToken) -> None:
        if token.tenant_id != self._scope.tenant_id:
            raise RuntimeError("a refresh token can only be added to the unit of work's tenant")
        self._session.add(
            RefreshTokenRecord(
                id=token.id,
                tenant_id=token.tenant_id,
                user_id=token.user_id,
                family_id=token.family_id,
                token_digest=token.token_digest,
                expires_at=token.expires_at,
                family_expires_at=token.family_expires_at,
                used_at=token.used_at,
                revoked_at=token.revoked_at,
            )
        )

    async def claim(self, token_id: UUID, now: datetime) -> bool:
        # One conditional UPDATE: of two concurrent claims, the second matches no row.
        claimed = await self._session.scalar(
            update(RefreshTokenRecord)
            .where(
                RefreshTokenRecord.tenant_id == self._scope.tenant_id,
                RefreshTokenRecord.id == token_id,
                RefreshTokenRecord.used_at.is_(None),
                RefreshTokenRecord.revoked_at.is_(None),
            )
            .values(used_at=now)
            .returning(RefreshTokenRecord.id)
        )
        return claimed is not None

    async def revoke_family(self, family_id: UUID, now: datetime) -> None:
        await self._session.execute(
            update(RefreshTokenRecord)
            .where(
                RefreshTokenRecord.tenant_id == self._scope.tenant_id,
                RefreshTokenRecord.family_id == family_id,
                RefreshTokenRecord.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )


class SqlAlchemyIdentityLookup:
    """Cross-tenant by design, and only for authentication (ADR-0006)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def user_by_email(self, email: str) -> User | None:
        record = await self._session.scalar(select(UserRecord).where(UserRecord.email == email))
        return None if record is None else _to_user(record)

    async def refresh_token_by_digest(self, token_digest: str) -> RefreshToken | None:
        record = await self._session.scalar(
            select(RefreshTokenRecord).where(RefreshTokenRecord.token_digest == token_digest)
        )
        return None if record is None else _to_refresh_token(record)
