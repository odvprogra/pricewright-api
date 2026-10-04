"""Database tables as SQLAlchemy records. Repositories translate them to and from domain objects.

Check constraints repeat the domain's rules, so data written outside the application (seeds, manual
fixes) is held to the same invariants.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pricewright.domain.audit import ActorType, AuditValue
from pricewright.domain.catalog import MAX_CATEGORY_NAME_LENGTH
from pricewright.domain.users import MAX_EMAIL_LENGTH, Role
from pricewright.infrastructure.database import Base

_UUIDV7 = text("uuidv7()")
# ICU's root collation: names sort as people read them on every server (ADR-0014).
_UNICODE = "unicode"
_RATE = Numeric(5, 4)
_ROLES = ", ".join(f"'{role}'" for role in Role)
_ACTOR_TYPES = ", ".join(f"'{actor_type}'" for actor_type in ActorType)
# The API middleware accepts request IDs of up to 128 characters.
_REQUEST_ID_LENGTH = 128


class TenantRecord(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_is_iso_4217"),
        CheckConstraint("tax_rate >= 0 AND tax_rate < 1", name="tax_rate_in_range"),
        CheckConstraint(
            "approval_threshold >= 0 AND approval_threshold <= 1",
            name="approval_threshold_in_range",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUIDV7)
    name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(CHAR(3))
    tax_rate: Mapped[Decimal] = mapped_column(_RATE)
    approval_threshold: Mapped[Decimal] = mapped_column(_RATE)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserRecord(Base):
    __tablename__ = "users"
    __table_args__ = (
        # Target of composite foreign keys, so child rows cannot cross tenants (ADR-0006).
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint("email = lower(email)", name="email_is_lowercase"),
        CheckConstraint(f"role IN ({_ROLES})", name="role_is_known"),
        CheckConstraint("failed_login_attempts >= 0", name="failed_login_attempts_not_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUIDV7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    email: Mapped[str] = mapped_column(String(MAX_EMAIL_LENGTH), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    failed_login_attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RefreshTokenRecord(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        # Composite: a session can only belong to a user of its own tenant (ADR-0006).
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"
        ),
        Index(None, "tenant_id", "family_id"),
        CheckConstraint("expires_at <= family_expires_at", name="expires_within_family"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUIDV7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    user_id: Mapped[uuid.UUID]
    family_id: Mapped[uuid.UUID]
    token_digest: Mapped[str] = mapped_column(CHAR(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    family_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ServiceAccountRecord(Base):
    __tablename__ = "service_accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),  # target of the API keys' composite foreign key
        UniqueConstraint("tenant_id", "name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUIDV7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(100))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(50)))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiKeyRecord(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        # Composite: a key can only belong to a service account of its own tenant (ADR-0006).
        ForeignKeyConstraint(
            ["tenant_id", "service_account_id"],
            ["service_accounts.tenant_id", "service_accounts.id"],
            ondelete="CASCADE",
        ),
        Index(None, "tenant_id", "service_account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUIDV7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    service_account_id: Mapped[uuid.UUID]
    key_digest: Mapped[str] = mapped_column(CHAR(64), unique=True)
    hint: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEventRecord(Base):
    """Append-only: a trigger rejects UPDATE and DELETE (migration ``add_audit_events``).

    Actor and resource ids have no foreign keys: they point to different tables, and the history
    must outlive what it describes.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),  # also the tenant's timeline, newest first
        Index(None, "tenant_id", "resource_type", "resource_id", "id"),  # a resource's history
        Index(None, "tenant_id", "actor_id", "id"),  # what someone did
        CheckConstraint(f"actor_type IN ({_ACTOR_TYPES})", name="actor_type_is_known"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUIDV7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[uuid.UUID]
    action: Mapped[str] = mapped_column(String(50))
    resource_type: Mapped[str] = mapped_column(String(30))
    resource_id: Mapped[uuid.UUID]
    changes: Mapped[dict[str, list[AuditValue]]] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(String(_REQUEST_ID_LENGTH))


class ProductCategoryRecord(Base):
    __tablename__ = "product_categories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),  # target of the products' composite foreign key
        Index(None, "tenant_id", "name", "id"),  # the list, by name
        Index(
            "uq_product_categories_tenant_id_lower_name",
            "tenant_id",
            func.lower(text("name")),
            unique=True,
        ),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUIDV7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(MAX_CATEGORY_NAME_LENGTH, collation=_UNICODE))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
