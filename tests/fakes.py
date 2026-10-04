"""In-memory fakes of the ports (handbook §7: don't mock what you own, write a fake).

They follow the same rules as the SQLAlchemy adapters, whose integration tests pin those rules down:
tenant scoping, explicit commits and unique emails.
"""

import copy
import dataclasses
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID

from pricewright.api.dependencies import Services
from pricewright.application.pagination import Keyset
from pricewright.application.ports import (
    ApiKeyRepository,
    AuditEventFilter,
    AuditEventRepository,
    CustomerQuery,
    CustomerRepository,
    CustomerSort,
    IdentityLookup,
    IssuedToken,
    PricingRuleQuery,
    PricingRuleRepository,
    PricingRuleSort,
    ProductCategoryRepository,
    ProductQuery,
    ProductRepository,
    ProductSort,
    RefreshTokenRepository,
    ServiceAccountRepository,
    TenantRepository,
    UnitOfWork,
    UserRepository,
)
from pricewright.domain.audit import AuditEvent
from pricewright.domain.auth import AuthenticationError, Principal
from pricewright.domain.catalog import Product, ProductCategory
from pricewright.domain.customers import Customer
from pricewright.domain.errors import ConflictError, StaleVersionError
from pricewright.domain.pricing_rules import PricingRule
from pricewright.domain.service_accounts import ApiKey, ServiceAccount
from pricewright.domain.sessions import RefreshToken
from pricewright.domain.tenants import Tenant
from pricewright.domain.users import Role, User
from pricewright.infrastructure.logging import current_request_id


@dataclass
class InMemoryDatabase:
    tenants: dict[UUID, Tenant] = field(default_factory=dict)
    users: dict[UUID, User] = field(default_factory=dict)
    refresh_tokens: dict[UUID, RefreshToken] = field(default_factory=dict)
    service_accounts: dict[UUID, ServiceAccount] = field(default_factory=dict)
    api_keys: dict[UUID, ApiKey] = field(default_factory=dict)
    audit_events: dict[UUID, AuditEvent] = field(default_factory=dict)
    product_categories: dict[UUID, ProductCategory] = field(default_factory=dict)
    products: dict[UUID, Product] = field(default_factory=dict)
    customers: dict[UUID, Customer] = field(default_factory=dict)
    pricing_rules: dict[UUID, PricingRule] = field(default_factory=dict)


class FakeTenantRepository:
    def __init__(self, tenants: dict[UUID, Tenant]) -> None:
        self._tenants = tenants

    async def add(self, tenant: Tenant) -> None:
        self._tenants[tenant.id] = tenant

    async def get(self, tenant_id: UUID) -> Tenant | None:
        return copy.deepcopy(self._tenants.get(tenant_id))

    async def save(self, tenant: Tenant) -> None:
        stored = self._tenants.get(tenant.id)
        if stored is None or stored.version != tenant.version:
            raise StaleVersionError("the tenant was changed by someone else; reload it")
        tenant.version += 1
        self._tenants[tenant.id] = copy.deepcopy(tenant)


class FakeUserRepository:
    def __init__(self, users: dict[UUID, User], uow: FakeUnitOfWork) -> None:
        self._users = users
        self._uow = uow

    async def add(self, user: User) -> None:
        if user.tenant_id != self._uow.tenant_id:
            raise RuntimeError("a user can only be added to the unit of work's tenant")
        self._users[user.id] = user

    async def get(self, user_id: UUID) -> User | None:
        user = self._users.get(user_id)
        if user is None or user.tenant_id != self._uow.tenant_id:
            return None
        return copy.deepcopy(user)  # like the adapter: changes need save()

    async def page(self, *, after: UUID | None, limit: int) -> list[User]:
        owned = sorted(
            (user for user in self._users.values() if user.tenant_id == self._uow.tenant_id),
            key=lambda user: user.id,
        )
        return copy.deepcopy([user for user in owned if after is None or user.id > after][:limit])

    def _stored(self, user: User) -> User:
        if user.tenant_id != self._uow.tenant_id:
            raise RuntimeError("only a user of the unit of work's tenant can be saved")
        stored = self._users.get(user.id)
        if stored is None:
            raise RuntimeError("only an existing user can be saved")
        return stored

    async def save(self, user: User) -> None:
        stored = self._stored(user)
        if stored.version != user.version:
            raise StaleVersionError("the user was changed by someone else; reload it")
        stored.full_name, stored.role, stored.is_active = user.full_name, user.role, user.is_active
        stored.version += 1
        user.version = stored.version

    async def save_login_state(self, user: User) -> None:
        stored = self._stored(user)
        if stored.is_locked != user.is_locked:  # like the adapter: only `locked` is visible
            stored.version += 1
        stored.failed_login_attempts = user.failed_login_attempts
        stored.password_hash = user.password_hash
        user.version = stored.version

    async def lock_active_admins(self) -> list[UUID]:
        users = self._users.values()
        tenant_id = self._uow.tenant_id
        return sorted(u.id for u in users if u.tenant_id == tenant_id and u.is_active_admin)


class FakeRefreshTokenRepository:
    def __init__(self, tokens: dict[UUID, RefreshToken], uow: FakeUnitOfWork) -> None:
        self._tokens = tokens
        self._uow = uow

    def _owned(self) -> list[RefreshToken]:
        return [token for token in self._tokens.values() if token.tenant_id == self._uow.tenant_id]

    async def add(self, token: RefreshToken) -> None:
        if token.tenant_id != self._uow.tenant_id:
            raise RuntimeError("a refresh token can only be added to the unit of work's tenant")
        self._tokens[token.id] = token

    async def claim(self, token_id: UUID, now: datetime) -> bool:
        token = next((token for token in self._owned() if token.id == token_id), None)
        if token is None or token.used_at is not None or token.revoked_at is not None:
            return False
        token.used_at = now
        return True

    async def revoke_family(self, family_id: UUID, now: datetime) -> None:
        for token in self._owned():
            if token.family_id == family_id and token.revoked_at is None:
                token.revoked_at = now


class FakeServiceAccountRepository:
    def __init__(self, accounts: dict[UUID, ServiceAccount], uow: FakeUnitOfWork) -> None:
        self._accounts = accounts
        self._uow = uow

    async def add(self, account: ServiceAccount) -> None:
        if account.tenant_id != self._uow.tenant_id:
            raise RuntimeError("a service account can only be added to the unit of work's tenant")
        self._accounts[account.id] = account

    async def get(self, account_id: UUID, *, lock: bool = False) -> ServiceAccount | None:
        del lock  # one fake unit of work at a time: there is nothing to lock against
        account = self._accounts.get(account_id)
        if account is None or account.tenant_id != self._uow.tenant_id:
            return None
        return copy.deepcopy(account)

    async def page(self, *, after: UUID | None, limit: int) -> list[ServiceAccount]:
        owned = sorted(
            (a for a in self._accounts.values() if a.tenant_id == self._uow.tenant_id),
            key=lambda account: account.id,
        )
        return copy.deepcopy([a for a in owned if after is None or a.id > after][:limit])


class FakeApiKeyRepository:
    def __init__(self, keys: dict[UUID, ApiKey], uow: FakeUnitOfWork) -> None:
        self._keys = keys
        self._uow = uow

    async def add(self, key: ApiKey) -> None:
        if key.tenant_id != self._uow.tenant_id:
            raise RuntimeError("an API key can only be added to the unit of work's tenant")
        self._keys[key.id] = key

    async def get(self, key_id: UUID) -> ApiKey | None:
        key = self._keys.get(key_id)
        return None if key is None or key.tenant_id != self._uow.tenant_id else copy.deepcopy(key)

    async def active_for(self, account_id: UUID) -> list[ApiKey]:
        keys = (
            key
            for key in self._keys.values()
            if key.tenant_id == self._uow.tenant_id
            and key.service_account_id == account_id
            and key.revoked_at is None
        )
        return copy.deepcopy(sorted(keys, key=lambda key: key.id))

    async def save(self, key: ApiKey) -> None:
        stored = self._keys.get(key.id)
        if stored is not None and stored.tenant_id == self._uow.tenant_id:
            stored.last_used_at, stored.revoked_at = key.last_used_at, key.revoked_at


class FakeAuditEventRepository:
    def __init__(self, events: dict[UUID, AuditEvent], uow: FakeUnitOfWork) -> None:
        self._events = events
        self._uow = uow

    async def add(self, event: AuditEvent) -> None:
        if event.tenant_id != self._uow.tenant_id:
            raise RuntimeError("an audit event can only be added to the unit of work's tenant")
        request_id = event.request_id or current_request_id()
        self._events[event.id] = dataclasses.replace(event, request_id=request_id)

    async def page(
        self, where: AuditEventFilter, *, before: UUID | None, limit: int
    ) -> list[AuditEvent]:
        def matches(event: AuditEvent) -> bool:
            return (
                event.tenant_id == self._uow.tenant_id
                and where.resource_type in {None, event.resource_type}
                and where.resource_id in {None, event.resource_id}
                and where.actor_id in {None, event.actor_id}
                and where.action in {None, event.action}
                and (before is None or event.id < before)
            )

        newest_first = sorted(self._events.values(), key=lambda event: event.id, reverse=True)
        return [event for event in newest_first if matches(event)][:limit]


def text_key(text: str) -> str:
    """Close to the database's Unicode collation for the tests' names: case does not decide."""
    return text.casefold()


class FakeProductCategoryRepository:
    def __init__(self, categories: dict[UUID, ProductCategory], uow: FakeUnitOfWork) -> None:
        self._categories = categories
        self._uow = uow

    def _owned(self) -> list[ProductCategory]:
        tenant_id = self._uow.tenant_id
        return [c for c in self._categories.values() if c.tenant_id == tenant_id]

    async def add(self, category: ProductCategory) -> None:
        if category.tenant_id != self._uow.tenant_id:
            raise RuntimeError("a category can only be added to the unit of work's tenant")
        self._categories[category.id] = category

    async def get(self, category_id: UUID) -> ProductCategory | None:
        found = next((c for c in self._owned() if c.id == category_id), None)
        return copy.deepcopy(found)

    async def named(self, name: str) -> ProductCategory | None:
        found = next((c for c in self._owned() if c.name.lower() == name.lower()), None)
        return copy.deepcopy(found)

    async def page(self, *, after: Keyset | None, limit: int) -> list[ProductCategory]:
        def key(category: ProductCategory) -> tuple[str, UUID]:
            return text_key(category.name), category.id

        ordered = sorted(self._owned(), key=key)
        if after is not None:
            start = (text_key(after.value or ""), after.id)
            ordered = [category for category in ordered if key(category) > start]
        return copy.deepcopy(ordered[:limit])

    async def save(self, category: ProductCategory) -> None:
        stored = self._categories.get(category.id)
        if stored is None or stored.tenant_id != self._uow.tenant_id:
            raise RuntimeError("only a category of the unit of work's tenant can be saved")
        if stored.version != category.version:
            raise StaleVersionError("the category was changed by someone else; reload it")
        stored.name, stored.version = category.name, stored.version + 1
        category.version = stored.version


class FakeProductRepository:
    def __init__(self, products: dict[UUID, Product], uow: FakeUnitOfWork) -> None:
        self._products = products
        self._uow = uow

    def _owned(self) -> list[Product]:
        return [p for p in self._products.values() if p.tenant_id == self._uow.tenant_id]

    async def add(self, product: Product) -> None:
        if product.tenant_id != self._uow.tenant_id:
            raise RuntimeError("only a product of the unit of work's tenant can be stored")
        self._products[product.id] = product

    async def get(self, product_id: UUID) -> Product | None:
        return copy.deepcopy(next((p for p in self._owned() if p.id == product_id), None))

    async def with_sku(self, sku: str) -> Product | None:
        found = next((p for p in self._owned() if p.sku.lower() == sku.lower()), None)
        return copy.deepcopy(found)

    async def page(self, query: ProductQuery, *, after: Keyset | None, limit: int) -> list[Product]:
        def matches(product: Product) -> bool:
            text = None if query.text is None else query.text.lower()
            return (
                (text is None or text in product.sku.lower() or text in product.name.lower())
                and (query.sku is None or product.sku.lower() == query.sku.lower())
                and query.category_id in {None, product.category_id}
                and query.active in {None, product.is_active}
            )

        def key(value: str | None, product_id: UUID) -> tuple[str, UUID]:
            return ("" if query.sort is ProductSort.CREATED else text_key(value or "")), product_id

        def value(product: Product) -> str | None:
            return {ProductSort.SKU: product.sku, ProductSort.NAME: product.name}.get(query.sort)

        return keyset_page(
            [p for p in self._owned() if matches(p)],
            lambda product: key(value(product), product.id),
            None if after is None else key(after.value, after.id),
            descending=query.descending,
            limit=limit,
        )

    async def save(self, product: Product) -> None:
        stored = self._products.get(product.id)
        if stored is None or stored.tenant_id != self._uow.tenant_id:
            raise RuntimeError("only a product of the unit of work's tenant can be stored")
        if stored.version != product.version:
            raise StaleVersionError("the product was changed by someone else; reload it")
        product.version = stored.version + 1
        self._products[product.id] = copy.deepcopy(product)


class FakePricingRuleRepository:
    def __init__(self, rules: dict[UUID, PricingRule], uow: FakeUnitOfWork) -> None:
        self._rules = rules
        self._uow = uow

    def _owned(self) -> list[PricingRule]:
        return [rule for rule in self._rules.values() if rule.tenant_id == self._uow.tenant_id]

    async def add(self, rule: PricingRule) -> None:
        if rule.tenant_id != self._uow.tenant_id:
            raise RuntimeError("only a pricing rule of the unit of work's tenant can be stored")
        self._rules[rule.id] = rule

    async def get(self, rule_id: UUID) -> PricingRule | None:
        return copy.deepcopy(next((rule for rule in self._owned() if rule.id == rule_id), None))

    async def page(
        self, query: PricingRuleQuery, *, after: Keyset | None, limit: int
    ) -> list[PricingRule]:
        def matches(rule: PricingRule) -> bool:
            return (
                query.kind in {None, rule.kind}
                and query.product_id in {None, rule.product_id}
                and query.category_id in {None, rule.category_id}
                and query.customer_tier in {None, rule.customer_tier}
                and query.active in {None, rule.is_active}
            )

        def key(value: str | None, rule_id: UUID) -> tuple[str, UUID]:
            by_name = query.sort is PricingRuleSort.NAME
            return (text_key(value or "") if by_name else ""), rule_id

        return keyset_page(
            [rule for rule in self._owned() if matches(rule)],
            lambda rule: key(rule.name, rule.id),
            None if after is None else key(after.value, after.id),
            descending=query.descending,
            limit=limit,
        )

    async def effective_at(self, at: datetime) -> list[PricingRule]:
        effective = [rule for rule in self._owned() if rule.is_effective(at)]
        return copy.deepcopy(sorted(effective, key=lambda rule: rule.id))

    async def save(self, rule: PricingRule) -> None:
        stored = self._rules.get(rule.id)
        if stored is None or stored.tenant_id != self._uow.tenant_id:
            raise RuntimeError("only a pricing rule of the unit of work's tenant can be stored")
        if stored.version != rule.version:
            raise StaleVersionError("the pricing rule was changed by someone else; reload it")
        rule.version = stored.version + 1
        self._rules[rule.id] = copy.deepcopy(rule)


def keyset_page[T](
    rows: list[T],
    position: Callable[[T], tuple[str, UUID]],
    after: tuple[str, UUID] | None,
    *,
    descending: bool,
    limit: int,
) -> list[T]:
    """Sort by ``position`` and continue after ``after``, like the adapters' keyset queries."""
    ordered = sorted(rows, key=position, reverse=descending)
    if after is not None:
        ordered = [
            row
            for row in ordered
            if (position(row) < after if descending else position(row) > after)
        ]
    return copy.deepcopy(ordered[:limit])


class FakeCustomerRepository:
    def __init__(self, customers: dict[UUID, Customer], uow: FakeUnitOfWork) -> None:
        self._customers = customers
        self._uow = uow

    def _owned(self) -> list[Customer]:
        return [c for c in self._customers.values() if c.tenant_id == self._uow.tenant_id]

    async def add(self, customer: Customer) -> None:
        if customer.tenant_id != self._uow.tenant_id:
            raise RuntimeError("only a customer of the unit of work's tenant can be stored")
        self._customers[customer.id] = customer

    async def get(self, customer_id: UUID) -> Customer | None:
        return copy.deepcopy(next((c for c in self._owned() if c.id == customer_id), None))

    async def with_account_number(self, account_number: str) -> Customer | None:
        wanted = account_number.lower()
        found = next((c for c in self._owned() if c.account_number.lower() == wanted), None)
        return copy.deepcopy(found)

    async def page(
        self, query: CustomerQuery, *, after: Keyset | None, limit: int
    ) -> list[Customer]:
        text = None if query.text is None else query.text.lower()

        def matches(customer: Customer) -> bool:
            return (
                (
                    text is None
                    or text in customer.account_number.lower()
                    or text in customer.name.lower()
                )
                and query.tax_id in {None, customer.tax_id}
                and query.tier in {None, customer.tier}
                and query.active in {None, customer.is_active}
            )

        def value(customer: Customer) -> str | None:
            values = {CustomerSort.ACCOUNT_NUMBER: customer.account_number}
            return (values | {CustomerSort.NAME: customer.name}).get(query.sort)

        def key(sort_value: str | None, row_id: UUID) -> tuple[str, UUID]:
            return text_key(sort_value or ""), row_id

        return keyset_page(
            [c for c in self._owned() if matches(c)],
            lambda customer: key(value(customer), customer.id),
            None if after is None else key(after.value, after.id),
            descending=query.descending,
            limit=limit,
        )

    async def save(self, customer: Customer) -> None:
        stored = self._customers.get(customer.id)
        if stored is None or stored.tenant_id != self._uow.tenant_id:
            raise RuntimeError("only a customer of the unit of work's tenant can be stored")
        if stored.version != customer.version:
            raise StaleVersionError("the customer was changed by someone else; reload it")
        customer.version = stored.version + 1
        self._customers[customer.id] = copy.deepcopy(customer)


class FakeIdentityLookup:
    def __init__(self, database: InMemoryDatabase) -> None:
        self._database = database

    async def user_by_email(self, email: str) -> User | None:
        users = self._database.users.values()
        return copy.deepcopy(next((user for user in users if user.email == email), None))

    async def refresh_token_by_digest(self, token_digest: str) -> RefreshToken | None:
        tokens = self._database.refresh_tokens.values()
        found = next((token for token in tokens if token.token_digest == token_digest), None)
        return copy.deepcopy(found)

    async def api_key_by_digest(self, key_digest: str) -> ApiKey | None:
        keys = self._database.api_keys.values()
        return copy.deepcopy(next((key for key in keys if key.key_digest == key_digest), None))


class FakeUnitOfWork:
    """Works on a copy of the database; ``commit`` writes the copy back."""

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
    identities: IdentityLookup

    def __init__(self, database: InMemoryDatabase) -> None:
        self._database = database
        self._tenant_id: UUID | None = None

    async def __aenter__(self) -> Self:
        self._staged = copy.deepcopy(self._database)
        self.tenants = FakeTenantRepository(self._staged.tenants)
        self.users = FakeUserRepository(self._staged.users, self)
        self.refresh_tokens = FakeRefreshTokenRepository(self._staged.refresh_tokens, self)
        self.service_accounts = FakeServiceAccountRepository(self._staged.service_accounts, self)
        self.api_keys = FakeApiKeyRepository(self._staged.api_keys, self)
        self.audit_events = FakeAuditEventRepository(self._staged.audit_events, self)
        self.product_categories = FakeProductCategoryRepository(
            self._staged.product_categories, self
        )
        self.products = FakeProductRepository(self._staged.products, self)
        self.customers = FakeCustomerRepository(self._staged.customers, self)
        self.pricing_rules = FakePricingRuleRepository(self._staged.pricing_rules, self)
        self.identities = FakeIdentityLookup(self._staged)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del self._staged

    @property
    def tenant_id(self) -> UUID:
        if self._tenant_id is None:
            raise RuntimeError("tenant-owned data accessed before the unit of work was bound")
        return self._tenant_id

    def bind_tenant(self, tenant_id: UUID) -> None:
        if self._tenant_id is not None and self._tenant_id != tenant_id:
            raise RuntimeError("the unit of work is already bound to another tenant")
        self._tenant_id = tenant_id

    async def commit(self) -> None:
        # The same unique constraints as the database: emails, account names per tenant, and
        # category names and SKUs per tenant ignoring case.
        staged = self._staged
        emails = [user.email for user in staged.users.values()]
        names = [(a.tenant_id, a.name) for a in staged.service_accounts.values()]
        categories = [(c.tenant_id, c.name.lower()) for c in staged.product_categories.values()]
        skus = [(p.tenant_id, p.sku.lower()) for p in staged.products.values()]
        accounts = [(c.tenant_id, c.account_number.lower()) for c in staged.customers.values()]
        unique_keys = (emails, names, categories, skus, accounts)
        if any(len(keys) != len(set(keys)) for keys in unique_keys):
            raise ConflictError("the change conflicts with an existing record")
        self._database.tenants = copy.deepcopy(self._staged.tenants)
        self._database.users = copy.deepcopy(self._staged.users)
        self._database.refresh_tokens = copy.deepcopy(self._staged.refresh_tokens)
        self._database.service_accounts = copy.deepcopy(self._staged.service_accounts)
        self._database.api_keys = copy.deepcopy(self._staged.api_keys)
        self._database.audit_events = copy.deepcopy(self._staged.audit_events)
        self._database.product_categories = copy.deepcopy(self._staged.product_categories)
        self._database.products = copy.deepcopy(self._staged.products)
        self._database.customers = copy.deepcopy(self._staged.customers)


class FakePasswordHasher:
    """Readable and instant: ``hash("secret")`` is ``"hashed:secret"``.

    Hashes without the prefix count as made with old parameters (``needs_rehash``), and every
    verification is recorded, so tests can check that unknown users cost the same work.
    """

    PREFIX = "hashed:"

    def __init__(self) -> None:
        self.verified: list[str] = []

    async def hash(self, password: str) -> str:
        return self.PREFIX + password

    async def verify(self, password_hash: str, password: str) -> bool:
        self.verified.append(password)
        return password_hash in {self.PREFIX + password, "legacy:" + password}

    async def verify_unknown(self, password: str) -> None:
        self.verified.append(password)

    def needs_rehash(self, password_hash: str) -> bool:
        return not password_hash.startswith(self.PREFIX)


class FakeClock:
    """A clock that only moves when the test says so."""

    def __init__(self, now: datetime = datetime(2026, 10, 3, 12, tzinfo=UTC)) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class FakeAccessTokens:
    """Tokens are ``token:<tenant>:<user>:<role>``; anything else is rejected."""

    EXPIRES_IN = 900

    def issue(self, principal: Principal) -> IssuedToken:
        token = f"token:{principal.tenant_id}:{principal.subject_id}:{principal.role}"
        return IssuedToken(token=token, expires_in=self.EXPIRES_IN)

    def read(self, token: str) -> Principal:
        try:
            prefix, tenant_id, user_id, role = token.split(":")
            if prefix != "token":
                raise ValueError(prefix)
            return Principal(uuid.UUID(tenant_id), uuid.UUID(user_id), Role(role))
        except ValueError as error:
            raise AuthenticationError("invalid access token") from error


def fake_services(
    database: InMemoryDatabase | None = None, clock: FakeClock | None = None
) -> Services:
    """API services backed by the fakes: an app that runs without infrastructure."""
    shared = database if database is not None else InMemoryDatabase()

    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(shared)

    return Services(
        unit_of_work=unit_of_work,
        hasher=FakePasswordHasher(),
        access_tokens=FakeAccessTokens(),
        clock=clock if clock is not None else FakeClock(),
    )
