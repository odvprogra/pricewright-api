"""Database tables as SQLAlchemy records. Repositories translate them to and from domain objects.

Check constraints repeat the domain's rules, so data written outside the application (seeds, manual
fixes) is held to the same invariants.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pricewright.domain.audit import ActorType, AuditValue
from pricewright.domain.catalog import (
    MAX_CATEGORY_NAME_LENGTH,
    MAX_PRODUCT_NAME_LENGTH,
    MAX_SKU_LENGTH,
    UnitOfMeasure,
)
from pricewright.domain.customers import (
    MAX_ACCOUNT_NUMBER_LENGTH,
    MAX_CUSTOMER_NAME_LENGTH,
    MAX_PAYMENT_TERMS_DAYS,
    MAX_TAX_ID_LENGTH,
    CustomerTier,
)
from pricewright.domain.pricing import MAX_REASON_LENGTH as MAX_OVERRIDE_REASON_LENGTH
from pricewright.domain.pricing_rules import MAX_RULE_NAME_LENGTH, RuleKind
from pricewright.domain.quote_approvals import MAX_COMMENT_LENGTH, ApprovalStatus
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.quotes import MAX_NOTES_LENGTH
from pricewright.domain.quotes import MAX_REASON_LENGTH as MAX_CANCEL_REASON_LENGTH
from pricewright.domain.tenants import (
    MAX_QUOTE_PREFIX_LENGTH,
    MAX_QUOTE_VALIDITY_DAYS,
    QUOTE_PREFIX_PATTERN,
)
from pricewright.domain.users import MAX_EMAIL_LENGTH, Role
from pricewright.infrastructure.database import Base

_UUIDV7 = text("uuidv7()")
# ICU's root collation: names sort as people read them on every server (ADR-0014).
_UNICODE = "unicode"
_RATE = Numeric(5, 4)
_MONEY = Numeric(18, 4)  # ADR-0003
_ROLES = ", ".join(f"'{role}'" for role in Role)
_ACTOR_TYPES = ", ".join(f"'{actor_type}'" for actor_type in ActorType)
_UNITS = ", ".join(f"'{unit}'" for unit in UnitOfMeasure)
_TIERS = ", ".join(f"'{tier}'" for tier in CustomerTier)
_RULE_KINDS = ", ".join(f"'{kind}'" for kind in RuleKind)
_QUOTE_STATUSES = ", ".join(f"'{status}'" for status in QuoteStatus)
_APPROVAL_STATUSES = ", ".join(f"'{status}'" for status in ApprovalStatus)
_OVERRIDE_KINDS = "'rate', 'price'"
# A discount is 1 - net / list: negative when an override raises prices far above the list.
_DISCOUNT = Numeric(21, 4)
# Prefix (5), year (4), sequence (6 or more) and two hyphens.
_QUOTE_NUMBER_LENGTH = 24
_QUANTITY = Numeric(10, 3)  # domain/quantities.py
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
        CheckConstraint(
            f"quote_prefix ~ '^{QUOTE_PREFIX_PATTERN.pattern}$'", name="quote_prefix_is_valid"
        ),
        CheckConstraint(
            f"quote_validity_days BETWEEN 1 AND {MAX_QUOTE_VALIDITY_DAYS}",
            name="quote_validity_days_in_range",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        # Target of the composite foreign keys that keep every price in the tenant's currency.
        UniqueConstraint("id", "currency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUIDV7)
    name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(CHAR(3))
    tax_rate: Mapped[Decimal] = mapped_column(_RATE)
    approval_threshold: Mapped[Decimal] = mapped_column(_RATE)
    quote_prefix: Mapped[str] = mapped_column(String(MAX_QUOTE_PREFIX_LENGTH))
    quote_validity_days: Mapped[int] = mapped_column(SmallInteger)
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


class ProductRecord(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),  # target of quote lines' composite foreign key (M4)
        # The tenant's currency (ADR-0003); this key also proves the tenant exists.
        ForeignKeyConstraint(["tenant_id", "currency"], ["tenants.id", "tenants.currency"]),
        # A category of the same tenant (ADR-0006); a product may have none.
        ForeignKeyConstraint(
            ["tenant_id", "category_id"], ["product_categories.tenant_id", "product_categories.id"]
        ),
        Index("uq_products_tenant_id_lower_sku", "tenant_id", func.lower(text("sku")), unique=True),
        Index(None, "tenant_id", "sku", "id"),  # the list by SKU
        Index(None, "tenant_id", "name", "id"),  # the list by name
        Index(None, "tenant_id", "category_id"),
        CheckConstraint(f"unit IN ({_UNITS})", name="unit_is_known"),
        CheckConstraint("list_price >= 0", name="list_price_not_negative"),
        CheckConstraint("unit_cost >= 0", name="unit_cost_not_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUIDV7)
    tenant_id: Mapped[uuid.UUID]
    sku: Mapped[str] = mapped_column(String(MAX_SKU_LENGTH, collation=_UNICODE))
    name: Mapped[str] = mapped_column(String(MAX_PRODUCT_NAME_LENGTH, collation=_UNICODE))
    category_id: Mapped[uuid.UUID | None]
    unit: Mapped[str] = mapped_column(String(3))
    currency: Mapped[str] = mapped_column(CHAR(3))
    list_price: Mapped[Decimal] = mapped_column(_MONEY)
    unit_cost: Mapped[Decimal] = mapped_column(_MONEY)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CustomerRecord(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),  # target of quotes' composite foreign key (M4)
        Index(
            "uq_customers_tenant_id_lower_account_number",
            "tenant_id",
            func.lower(text("account_number")),
            unique=True,
        ),
        Index(None, "tenant_id", "account_number", "id"),  # the list by account number
        Index(None, "tenant_id", "name", "id"),  # the list by name
        Index(None, "tenant_id", "tax_id"),  # matching documents to customers (ops-copilot)
        CheckConstraint(f"tier IN ({_TIERS})", name="tier_is_known"),
        CheckConstraint(
            f"payment_terms_days BETWEEN 0 AND {MAX_PAYMENT_TERMS_DAYS}",
            name="payment_terms_days_in_range",
        ),
        CheckConstraint("tax_id ~ '^[A-Z0-9]+$'", name="tax_id_is_compact"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUIDV7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    account_number: Mapped[str] = mapped_column(
        String(MAX_ACCOUNT_NUMBER_LENGTH, collation=_UNICODE)
    )
    name: Mapped[str] = mapped_column(String(MAX_CUSTOMER_NAME_LENGTH, collation=_UNICODE))
    tax_id: Mapped[str | None] = mapped_column(String(MAX_TAX_ID_LENGTH))
    tier: Mapped[str] = mapped_column(String(10))
    payment_terms_days: Mapped[int] = mapped_column(SmallInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PricingRuleRecord(Base):
    """One row per rule, whatever its kind (ADR-0018); checks hold each kind's shape."""

    __tablename__ = "pricing_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),  # target of the brackets' composite foreign key
        # A product or a category of the same tenant (ADR-0006), or neither: every product.
        ForeignKeyConstraint(["tenant_id", "product_id"], ["products.tenant_id", "products.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "category_id"], ["product_categories.tenant_id", "product_categories.id"]
        ),
        Index(None, "tenant_id", "name", "id"),  # the list by name
        Index(None, "tenant_id", "product_id"),
        Index(None, "tenant_id", "category_id"),
        CheckConstraint(f"kind IN ({_RULE_KINDS})", name="kind_is_known"),
        CheckConstraint("product_id IS NULL OR category_id IS NULL", name="one_scope"),
        CheckConstraint(f"customer_tier IN ({_TIERS})", name="customer_tier_is_known"),
        CheckConstraint(
            "(customer_tier IS NOT NULL) = (kind = 'customer_tier')",
            name="customer_tier_only_on_tier_discounts",
        ),
        CheckConstraint("(rate IS NULL) = (kind = 'volume_tier')", name="rate_unless_volume_tier"),
        CheckConstraint(
            "CASE WHEN kind = 'margin_floor' THEN rate >= 0 AND rate < 1 "
            "ELSE rate > 0 AND rate <= 1 END",
            name="rate_in_range",
        ),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="window_is_ordered"),
        CheckConstraint("kind <> 'promotion' OR valid_to IS NOT NULL", name="promotions_end"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=_UUIDV7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(MAX_RULE_NAME_LENGTH, collation=_UNICODE))
    product_id: Mapped[uuid.UUID | None]
    category_id: Mapped[uuid.UUID | None]
    customer_tier: Mapped[str | None] = mapped_column(String(10))
    rate: Mapped[Decimal | None] = mapped_column(_RATE)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PricingRuleBracketRecord(Base):
    """A volume tier's bracket: from ``min_quantity`` on, ``rate`` off. Replaced as a set."""

    __tablename__ = "pricing_rule_brackets"
    __table_args__ = (
        # A bracket belongs to a rule of its own tenant (ADR-0006) and goes with it.
        ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            ["pricing_rules.tenant_id", "pricing_rules.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("min_quantity > 0", name="min_quantity_positive"),
        CheckConstraint("rate > 0 AND rate <= 1", name="rate_in_range"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    rule_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    min_quantity: Mapped[Decimal] = mapped_column(_QUANTITY, primary_key=True)
    rate: Mapped[Decimal] = mapped_column(_RATE)


class QuoteRecord(Base):
    """One revision of a quote (ADR-0005), with its totals as last priced (ADR-0019)."""

    __tablename__ = "quotes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),  # target of lines, approvals and revision links
        # Revisions of a quote share its number (decision D-07).
        UniqueConstraint("tenant_id", "number", "revision"),
        # The tenant's currency (ADR-0003); this key also proves the tenant exists.
        ForeignKeyConstraint(["tenant_id", "currency"], ["tenants.id", "tenants.currency"]),
        ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
        # Each revision has at most one predecessor and one successor, of its own tenant.
        ForeignKeyConstraint(["tenant_id", "supersedes_id"], ["quotes.tenant_id", "quotes.id"]),
        # Checked at commit: a revise saves the old revision (compare-and-set) before adding the
        # new one, so of two concurrent revisions the second fails on the version (ADR-0012).
        ForeignKeyConstraint(
            ["tenant_id", "superseded_by_id"],
            ["quotes.tenant_id", "quotes.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint("tenant_id", "supersedes_id"),
        UniqueConstraint("tenant_id", "superseded_by_id"),
        CheckConstraint(f"status IN ({_QUOTE_STATUSES})", name="status_is_known"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(
            "(revision = 1) = (supersedes_id IS NULL)", name="later_revisions_link_back"
        ),
        CheckConstraint(
            "(status = 'superseded') = (superseded_by_id IS NOT NULL)",
            name="superseded_links_forward",
        ),
        CheckConstraint(
            "(status = 'cancelled') = (cancel_reason IS NOT NULL)", name="cancelled_with_a_reason"
        ),
        CheckConstraint(f"created_by_type IN ({_ACTOR_TYPES})", name="created_by_type_is_known"),
        CheckConstraint(
            f"submitted_by_type IN ({_ACTOR_TYPES})", name="submitted_by_type_is_known"
        ),
        CheckConstraint(
            "(submitted_by_type IS NULL) = (submitted_by_id IS NULL) "
            "AND (submitted_by_id IS NULL) = (submitted_at IS NULL)",
            name="submitted_by_and_at_together",
        ),
        CheckConstraint("total = net_subtotal + tax", name="total_adds_up"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    number: Mapped[str] = mapped_column(String(_QUOTE_NUMBER_LENGTH))
    revision: Mapped[int] = mapped_column(SmallInteger)
    customer_id: Mapped[uuid.UUID]
    currency: Mapped[str] = mapped_column(CHAR(3))
    status: Mapped[str] = mapped_column(String(20))
    valid_until: Mapped[date] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(String(MAX_NOTES_LENGTH))
    created_by_type: Mapped[str] = mapped_column(String(20))
    created_by_id: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_by_type: Mapped[str | None] = mapped_column(String(20))
    submitted_by_id: Mapped[uuid.UUID | None]
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(MAX_CANCEL_REASON_LENGTH))
    supersedes_id: Mapped[uuid.UUID | None]
    superseded_by_id: Mapped[uuid.UUID | None]
    list_subtotal: Mapped[Decimal] = mapped_column(_MONEY)
    net_subtotal: Mapped[Decimal] = mapped_column(_MONEY)
    tax_rate: Mapped[Decimal] = mapped_column(_RATE)
    tax: Mapped[Decimal] = mapped_column(_MONEY)
    total: Mapped[Decimal] = mapped_column(_MONEY)
    approval_threshold: Mapped[Decimal] = mapped_column(_RATE)
    priced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class QuoteLineRecord(Base):
    """A line and the engine's snapshot of its price; the steps of the waterfall as JSON."""

    __tablename__ = "quote_lines"
    __table_args__ = (
        # A line belongs to a quote of its own tenant (ADR-0006) and goes with it.
        ForeignKeyConstraint(
            ["tenant_id", "quote_id"], ["quotes.tenant_id", "quotes.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(["tenant_id", "product_id"], ["products.tenant_id", "products.id"]),
        ForeignKeyConstraint(["tenant_id", "currency"], ["tenants.id", "tenants.currency"]),
        UniqueConstraint("tenant_id", "quote_id", "position"),
        CheckConstraint(f"unit IN ({_UNITS})", name="unit_is_known"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("jsonb_typeof(steps) = 'array'", name="steps_are_a_list"),
        CheckConstraint(
            "(margin_floor_rule_id IS NULL) = (margin_floor_rate IS NULL) "
            "AND (margin_floor_rate IS NULL) = (margin_floor_label IS NULL)",
            name="margin_floor_whole",
        ),
        CheckConstraint(f"override_kind IN ({_OVERRIDE_KINDS})", name="override_kind_is_known"),
        CheckConstraint(
            "CASE override_kind "
            "WHEN 'rate' THEN override_rate IS NOT NULL AND override_unit_price IS NULL "
            "WHEN 'price' THEN override_unit_price IS NOT NULL AND override_rate IS NULL "
            "ELSE override_rate IS NULL AND override_unit_price IS NULL END "
            "AND (override_kind IS NULL) = (override_reason IS NULL) "
            "AND (override_kind IS NULL) = (override_by IS NULL)",
            name="override_whole",
        ),
        CheckConstraint(f"added_by_type IN ({_ACTOR_TYPES})", name="added_by_type_is_known"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    quote_id: Mapped[uuid.UUID]
    position: Mapped[int] = mapped_column(SmallInteger)
    product_id: Mapped[uuid.UUID]
    sku: Mapped[str] = mapped_column(String(MAX_SKU_LENGTH))
    product_name: Mapped[str] = mapped_column(String(MAX_PRODUCT_NAME_LENGTH))
    unit: Mapped[str] = mapped_column(String(3))
    quantity: Mapped[Decimal] = mapped_column(_QUANTITY)
    currency: Mapped[str] = mapped_column(CHAR(3))
    list_unit_price: Mapped[Decimal] = mapped_column(_MONEY)
    steps: Mapped[list[dict[str, str | None]]] = mapped_column(JSONB)
    list_total: Mapped[Decimal] = mapped_column(_MONEY)
    net_total: Mapped[Decimal] = mapped_column(_MONEY)
    cost_total: Mapped[Decimal] = mapped_column(_MONEY)
    margin_floor_rule_id: Mapped[uuid.UUID | None]
    margin_floor_label: Mapped[str | None] = mapped_column(String(MAX_RULE_NAME_LENGTH))
    margin_floor_rate: Mapped[Decimal | None] = mapped_column(_RATE)
    override_kind: Mapped[str | None] = mapped_column(String(5))
    override_rate: Mapped[Decimal | None] = mapped_column(_RATE)
    override_unit_price: Mapped[Decimal | None] = mapped_column(_MONEY)
    override_reason: Mapped[str | None] = mapped_column(String(MAX_OVERRIDE_REASON_LENGTH))
    override_by: Mapped[uuid.UUID | None]
    added_by_type: Mapped[str] = mapped_column(String(20))
    added_by_id: Mapped[uuid.UUID]


class ApprovalRequestRecord(Base):
    """An approval request and its decision (ADR-0020); kept as long as its quote."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "quote_id"], ["quotes.tenant_id", "quotes.id"], ondelete="CASCADE"
        ),
        # Only people decide, and only people of the same tenant.
        ForeignKeyConstraint(["tenant_id", "decided_by"], ["users.tenant_id", "users.id"]),
        ForeignKeyConstraint(["tenant_id", "currency"], ["tenants.id", "tenants.currency"]),
        Index(
            "uq_approval_requests_one_pending_per_quote",
            "tenant_id",
            "quote_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index(None, "tenant_id", "quote_id"),
        CheckConstraint(f"status IN ({_APPROVAL_STATUSES})", name="status_is_known"),
        CheckConstraint(
            f"requested_by_type IN ({_ACTOR_TYPES})", name="requested_by_type_is_known"
        ),
        CheckConstraint("cardinality(reasons) > 0", name="has_reasons"),
        CheckConstraint(
            "(status IN ('approved', 'rejected')) = (decided_by IS NOT NULL)",
            name="decided_by_a_person",
        ),
        CheckConstraint("(status = 'pending') = (decided_at IS NULL)", name="closed_at_a_time"),
        CheckConstraint("status <> 'rejected' OR comment IS NOT NULL", name="rejection_explained"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    quote_id: Mapped[uuid.UUID]
    requested_by_type: Mapped[str] = mapped_column(String(20))
    requested_by_id: Mapped[uuid.UUID]
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reasons: Mapped[list[str]] = mapped_column(ARRAY(String(40)))
    discount: Mapped[Decimal] = mapped_column(_DISCOUNT)
    approval_threshold: Mapped[Decimal] = mapped_column(_RATE)
    currency: Mapped[str] = mapped_column(CHAR(3))
    list_subtotal: Mapped[Decimal] = mapped_column(_MONEY)
    net_subtotal: Mapped[Decimal] = mapped_column(_MONEY)
    status: Mapped[str] = mapped_column(String(10))
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(String(MAX_COMMENT_LENGTH))


class QuoteNumberCounterRecord(Base):
    """The last quote number issued per tenant and year (ADR-0021); a rollback takes it back."""

    __tablename__ = "quote_number_counters"
    __table_args__ = (CheckConstraint("last_value >= 1", name="last_value_positive"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer)
