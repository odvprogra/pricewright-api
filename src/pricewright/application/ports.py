"""Ports: what use cases need from the outside world, as Protocols (ADR-0011).

Adapters in ``infrastructure`` implement them; tests use in-memory fakes.
"""

from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from pricewright.application.pagination import Keyset
from pricewright.domain.actors import Actor
from pricewright.domain.audit import AuditAction, AuditEvent, AuditResourceType
from pricewright.domain.auth import Principal
from pricewright.domain.catalog import Product, ProductCategory
from pricewright.domain.customers import Customer, CustomerTier
from pricewright.domain.money import Money
from pricewright.domain.pricing_rules import PricingRule, RuleKind
from pricewright.domain.quote_approvals import ApprovalRequest, ApprovalStatus
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.quotes import Quote
from pricewright.domain.service_accounts import ApiKey, ServiceAccount
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

    async def page(self, *, after: UUID | None, limit: int) -> list[User]:
        """Up to ``limit`` users ordered by id, starting after ``after`` (keyset pagination)."""
        ...

    async def save(self, user: User) -> None:
        """Store an admin's edits and bump ``user.version``, atomically.

        Raise ``StaleVersionError`` if the stored version is no longer ``user.version``.
        """
        ...

    async def save_login_state(self, user: User) -> None:
        """Store the sign-in fields (failed attempts, password hash) and bump the version.

        No version check: signing in never edits what an admin sees and changes.
        """
        ...

    async def lock_active_admins(self) -> list[UUID]:
        """Lock the tenant's active admins until commit and return their ids.

        Two admins demoting each other at once are serialized, so one admin always remains.
        """
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


class ServiceAccountRepository(Protocol):
    """Service accounts of the unit of work's tenant only (ADR-0006)."""

    async def add(self, account: ServiceAccount) -> None: ...

    async def get(self, account_id: UUID, *, lock: bool = False) -> ServiceAccount | None:
        """With ``lock``, the account stays locked until commit (serializes key issuance)."""
        ...

    async def page(self, *, after: UUID | None, limit: int) -> list[ServiceAccount]:
        """Up to ``limit`` accounts ordered by id, starting after ``after``."""
        ...


class ApiKeyRepository(Protocol):
    """API keys of the unit of work's tenant only (ADR-0006)."""

    async def add(self, key: ApiKey) -> None: ...

    async def get(self, key_id: UUID) -> ApiKey | None: ...

    async def active_for(self, account_id: UUID) -> list[ApiKey]:
        """The account's keys that are not revoked, oldest first."""
        ...

    async def save(self, key: ApiKey) -> None:
        """Store the key's last use and revocation."""
        ...


class ProductCategoryRepository(Protocol):
    """Product categories of the unit of work's tenant only (ADR-0006)."""

    async def add(self, category: ProductCategory) -> None: ...

    async def get(self, category_id: UUID) -> ProductCategory | None: ...

    async def named(self, name: str) -> ProductCategory | None:
        """The category with this name, ignoring case."""
        ...

    async def page(self, *, after: Keyset | None, limit: int) -> list[ProductCategory]:
        """Up to ``limit`` categories by name (Unicode collation, ADR-0014), then id."""
        ...

    async def save(self, category: ProductCategory) -> None:
        """Store a rename and bump ``category.version``, atomically.

        Raise ``StaleVersionError`` if the stored version is no longer ``category.version``.
        """
        ...


class ProductSort(StrEnum):
    SKU = "sku"
    NAME = "name"
    CREATED = "created_at"


@dataclass(frozen=True, slots=True)
class ProductQuery:
    """Which products to list and in which order (ADR-0014). ``None`` filters match everything."""

    text: str | None = None
    """Contained in the SKU or the name, ignoring case."""
    sku: str | None = None
    """The exact SKU, ignoring case."""
    category_id: UUID | None = None
    active: bool | None = None
    sort: ProductSort = ProductSort.NAME
    descending: bool = False


class ProductRepository(Protocol):
    """Products of the unit of work's tenant only (ADR-0006)."""

    async def add(self, product: Product) -> None: ...

    async def get(self, product_id: UUID) -> Product | None: ...

    async def with_sku(self, sku: str) -> Product | None:
        """The product with this SKU, ignoring case."""
        ...

    async def with_ids(self, product_ids: Collection[UUID]) -> list[Product]:
        """The products with these ids, in no particular order; unknown ids are left out."""
        ...

    async def page(self, query: ProductQuery, *, after: Keyset | None, limit: int) -> list[Product]:
        """Up to ``limit`` matching products in the query's order, after ``after``.

        The keyset holds the sort value (none when sorted by creation) and the id.
        """
        ...

    async def save(self, product: Product) -> None:
        """Store changes and bump ``product.version``, atomically.

        Raise ``StaleVersionError`` if the stored version is no longer ``product.version``.
        """
        ...


class CustomerSort(StrEnum):
    ACCOUNT_NUMBER = "account_number"
    NAME = "name"
    CREATED = "created_at"


@dataclass(frozen=True, slots=True)
class CustomerQuery:
    """Which customers to list and in which order (ADR-0014). ``None`` filters match everything."""

    text: str | None = None
    """Contained in the account number or the name, ignoring case."""
    tax_id: str | None = None
    """The exact tax id, compact (``compact_tax_id``)."""
    tier: CustomerTier | None = None
    active: bool | None = None
    sort: CustomerSort = CustomerSort.NAME
    descending: bool = False


class CustomerRepository(Protocol):
    """Customers of the unit of work's tenant only (ADR-0006)."""

    async def add(self, customer: Customer) -> None: ...

    async def get(self, customer_id: UUID) -> Customer | None: ...

    async def with_account_number(self, account_number: str) -> Customer | None:
        """The customer with this account number, ignoring case."""
        ...

    async def page(
        self, query: CustomerQuery, *, after: Keyset | None, limit: int
    ) -> list[Customer]:
        """Up to ``limit`` matching customers in the query's order, after ``after``."""
        ...

    async def save(self, customer: Customer) -> None:
        """Store changes and bump ``customer.version``, atomically.

        Raise ``StaleVersionError`` if the stored version is no longer ``customer.version``.
        """
        ...


class PricingRuleSort(StrEnum):
    NAME = "name"
    CREATED = "created_at"


@dataclass(frozen=True, slots=True)
class PricingRuleQuery:
    """Which pricing rules to list and in which order (ADR-0014). ``None`` filters match all."""

    kind: RuleKind | None = None
    product_id: UUID | None = None
    category_id: UUID | None = None
    customer_tier: CustomerTier | None = None
    active: bool | None = None
    sort: PricingRuleSort = PricingRuleSort.NAME
    descending: bool = False


class PricingRuleRepository(Protocol):
    """Pricing rules of the unit of work's tenant only (ADR-0006), with their brackets."""

    async def add(self, rule: PricingRule) -> None: ...

    async def get(self, rule_id: UUID) -> PricingRule | None: ...

    async def page(
        self, query: PricingRuleQuery, *, after: Keyset | None, limit: int
    ) -> list[PricingRule]:
        """Up to ``limit`` matching rules in the query's order, after ``after``."""
        ...

    async def effective_at(self, at: datetime) -> list[PricingRule]:
        """Every active rule whose window holds ``at``: what the pricing engine applies."""
        ...

    async def save(self, rule: PricingRule) -> None:
        """Store changes, brackets included, and bump ``rule.version``, atomically.

        Raise ``StaleVersionError`` if the stored version is no longer ``rule.version``.
        """
        ...


class QuoteSort(StrEnum):
    CREATED = "created_at"
    VALID_UNTIL = "valid_until"


@dataclass(frozen=True, slots=True)
class QuoteQuery:
    """Which quotes to list and in which order (ADR-0014). ``None`` filters match everything."""

    status: QuoteStatus | None = None
    customer_id: UUID | None = None
    number: str | None = None
    """Every revision of this quote number."""
    created_by: UUID | None = None
    """A user's or a service account's id."""
    sort: QuoteSort = QuoteSort.CREATED
    descending: bool = True


@dataclass(frozen=True, slots=True)
class QuoteSummary:
    """A quote as lists show it, without its lines."""

    id: UUID
    number: str
    revision: int
    customer_id: UUID
    status: QuoteStatus
    valid_until: date
    net_subtotal: Money
    total: Money
    created_by: Actor
    created_at: datetime
    status_changed_at: datetime | None
    version: int

    @property
    def display_number(self) -> str:
        return self.number if self.revision == 1 else f"{self.number}-R{self.revision}"


@dataclass(frozen=True, slots=True)
class ApprovalSummary:
    """An approval request and the quote it is about, as the approval inbox shows them."""

    request: ApprovalRequest
    quote: QuoteSummary


class QuoteRepository(Protocol):
    """Quotes of the unit of work's tenant only (ADR-0006), with their lines and approvals."""

    async def allocate_number(self, year: int) -> int:
        """The tenant's next quote number in ``year``: 1, 2, 3... (ADR-0021).

        Concurrent allocations in the same tenant and year wait for each other until commit, and a
        rollback takes the number back, so the issued numbers have no gaps.
        """
        ...

    async def add(self, quote: Quote) -> None:
        """Store a new revision. Revising saves the old revision first, then adds the new one."""
        ...

    async def get(self, quote_id: UUID) -> Quote | None: ...

    async def page(
        self, query: QuoteQuery, *, after: Keyset | None, limit: int
    ) -> list[QuoteSummary]:
        """Up to ``limit`` matching quotes in the query's order, after ``after``.

        The keyset holds the sort value (the ISO date for ``valid_until``, none when sorted by
        creation) and the id.
        """
        ...

    async def approval_page(
        self, status: ApprovalStatus, *, today: date, after: UUID | None, limit: int
    ) -> list[ApprovalSummary]:
        """Up to ``limit`` requests with ``status``, oldest first, after the request ``after``.

        Pending requests of offers that expired before ``today`` are left out: they can only be
        revised (ADR-0005), so nobody decides on them.
        """
        ...

    async def save(self, quote: Quote) -> None:
        """Store changes, lines and approvals included, and bump ``quote.version``, atomically.

        Raise ``StaleVersionError`` if the stored version is no longer ``quote.version``.
        """
        ...


@dataclass(frozen=True, slots=True)
class AuditEventFilter:
    """Which audit events to list; a field left as ``None`` matches every event."""

    resource_type: AuditResourceType | None = None
    resource_id: UUID | None = None
    actor_id: UUID | None = None
    action: AuditAction | None = None


class AuditEventRepository(Protocol):
    """Audit events of the unit of work's tenant only (ADR-0006); append-only (ADR-0013)."""

    async def add(self, event: AuditEvent) -> None: ...

    async def page(
        self, where: AuditEventFilter, *, before: UUID | None, limit: int
    ) -> list[AuditEvent]:
        """Newest first: up to ``limit`` matching events older than ``before`` (keyset on id)."""
        ...


class IdentityLookup(Protocol):
    """The only cross-tenant reads: finding who is signing in before their tenant is known."""

    async def user_by_email(self, email: str) -> User | None: ...

    async def refresh_token_by_digest(self, token_digest: str) -> RefreshToken | None: ...

    async def api_key_by_digest(self, key_digest: str) -> ApiKey | None: ...


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

    Tenant-owned repositories (all but ``tenants`` and ``identities``) work only after
    ``bind_tenant``, and a unit of work can never be bound to a second tenant (ADR-0006).
    """

    tenants: TenantRepository
    users: UserRepository
    refresh_tokens: RefreshTokenRepository
    service_accounts: ServiceAccountRepository
    api_keys: ApiKeyRepository
    audit_events: AuditEventRepository
    product_categories: ProductCategoryRepository
    products: ProductRepository
    customers: CustomerRepository
    pricing_rules: PricingRuleRepository
    quotes: QuoteRepository
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
