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
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from pricewright.domain.users import MAX_EMAIL_LENGTH, Role
from pricewright.infrastructure.database import Base

_UUIDV7 = text("uuidv7()")
_RATE = Numeric(5, 4)
_ROLES = ", ".join(f"'{role}'" for role in Role)


class TenantRecord(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_is_iso_4217"),
        CheckConstraint("tax_rate >= 0 AND tax_rate < 1", name="tax_rate_in_range"),
        CheckConstraint(
            "approval_threshold >= 0 AND approval_threshold <= 1",
            name="approval_threshold_in_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUIDV7)
    name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(CHAR(3))
    tax_rate: Mapped[Decimal] = mapped_column(_RATE)
    approval_threshold: Mapped[Decimal] = mapped_column(_RATE)
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
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUIDV7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    email: Mapped[str] = mapped_column(String(MAX_EMAIL_LENGTH), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
