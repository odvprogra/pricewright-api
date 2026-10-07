"""Load the demo tenants through the use cases, as their people would (ADR-0024).

Nothing is written around the application: every record goes through the use case a person would
call, with that person's permissions, so validation, audit events and numbering are the real ones.
A simulated clock dates everything relative to the as-of date: the tenants go live
``GO_LIVE_DAYS`` before it.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from pricewright.application.catalog import NewProduct, create_category, create_product
from pricewright.application.customers import create_customer
from pricewright.application.onboarding import RegisterTenant, register_tenant
from pricewright.application.ports import PasswordHasher, UnitOfWorkFactory
from pricewright.application.pricing_rules import NewPricingRule, create_pricing_rule
from pricewright.application.users import NewUser, create_user
from pricewright.demo.tenants import DemoTenant, Person, RuleSpec, demo_tenants
from pricewright.domain.auth import Principal
from pricewright.domain.money import Money
from pricewright.domain.users import Role

DEFAULT_SEED = 2026
DEMO_PASSWORD = "pricewright demo"  # noqa: S105 - published on purpose, for local demo data
"""Every demo user's passphrase. Not a secret: it guards fictional data, and the seed refuses to
run outside local and test environments (ADR-0024)."""
GO_LIVE_DAYS = 190
GO_LIVE_HOUR = time(14, tzinfo=UTC)
STEP = timedelta(seconds=10)
"""How long each record takes to enter at go-live, so audit events keep their order in time."""


class DemoClock:
    """The time the demo's people act at; it only moves forward."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta = STEP) -> None:
        self.now += delta


@dataclass(frozen=True, slots=True)
class SeededTenant:
    name: str
    tenant_id: UUID
    quote_prefix: str
    order_prefix: str
    people: tuple[Person, ...]
    counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class SeedReport:
    as_of: date
    seed: int
    tenants: tuple[SeededTenant, ...]


@dataclass(slots=True)
class TenantLoader:
    """Loads one demo tenant, remembering who its people are and the ids of what they made."""

    spec: DemoTenant
    unit_of_work: UnitOfWorkFactory
    clock: DemoClock
    people: dict[str, Principal] = field(default_factory=dict)
    """By email."""
    categories: dict[str, UUID] = field(default_factory=dict)
    products: dict[str, UUID] = field(default_factory=dict)
    """By SKU."""
    customers: dict[str, UUID] = field(default_factory=dict)
    """By account number."""
    owners: dict[str, str] = field(default_factory=dict)
    """The email of the rep who manages each account."""
    rules: int = 0

    @property
    def admin(self) -> Principal:
        return self.people[self.spec.admin.email]

    @property
    def pricing_manager(self) -> Principal:
        """A sales manager keeps the rules (brief §2); without one, the admin does."""
        managers = self.spec.with_role(Role.SALES_MANAGER)
        return self.people[(managers or (self.spec.admin,))[0].email]

    async def register(self, hasher: PasswordHasher, password: str) -> None:
        spec, admin = self.spec, self.spec.admin
        registered = await register_tenant(
            RegisterTenant(
                name=spec.name,
                currency=spec.currency,
                tax_rate=spec.tax_rate,
                approval_threshold=spec.approval_threshold,
                quote_prefix=spec.quote_prefix,
                quote_validity_days=spec.quote_validity_days,
                order_prefix=spec.order_prefix,
                admin_email=admin.email,
                admin_full_name=admin.full_name,
                admin_password=password,
            ),
            unit_of_work=self.unit_of_work,
            hasher=hasher,
        )
        tenant_id = registered.tenant_id
        self.people[admin.email] = Principal(tenant_id, registered.admin_id, Role.ADMIN)
        for person in spec.people:
            new = NewUser(person.email, person.full_name, person.role, password)
            created = await create_user(
                self.admin, new, unit_of_work=self.unit_of_work, hasher=hasher, clock=self.clock
            )
            self.people[person.email] = Principal(tenant_id, created.value.id, person.role)
            self.clock.advance()

    async def load_catalog(self) -> None:
        for spec in self.spec.products:
            if spec.category not in self.categories:
                category = await create_category(
                    self.admin, name=spec.category, unit_of_work=self.unit_of_work, clock=self.clock
                )
                self.categories[spec.category] = category.value.id
            new = NewProduct(
                sku=spec.sku,
                name=spec.name,
                unit=spec.unit,
                list_price=Money(spec.list_price, self.spec.currency),
                unit_cost=Money(spec.unit_cost, self.spec.currency),
                category_id=self.categories[spec.category],
            )
            product = await create_product(
                self.admin, new, unit_of_work=self.unit_of_work, clock=self.clock
            )
            self.products[spec.sku] = product.value.id
            self.clock.advance()

    async def load_customers(self) -> None:
        """Each rep enters the accounts they manage, in turn."""
        reps = self.spec.with_role(Role.SALES_REP)
        for index, new in enumerate(self.spec.customers):
            owner = reps[index % len(reps)].email
            customer = await create_customer(
                self.people[owner], new, unit_of_work=self.unit_of_work, clock=self.clock
            )
            self.customers[new.account_number] = customer.value.id
            self.owners[new.account_number] = owner
            self.clock.advance()

    async def load_rules(self, as_of: date) -> None:
        for spec in self.spec.rules:
            await create_pricing_rule(
                self.pricing_manager,
                self._rule(spec, as_of),
                unit_of_work=self.unit_of_work,
                clock=self.clock,
            )
            self.rules += 1
            self.clock.advance()

    def _rule(self, spec: RuleSpec, as_of: date) -> NewPricingRule:
        def day(offset: int) -> datetime:
            return datetime.combine(as_of + timedelta(days=offset), time(tzinfo=UTC))

        return NewPricingRule(
            kind=spec.kind,
            name=spec.name,
            rate=spec.rate,
            brackets=spec.brackets,
            product_id=None if spec.sku is None else self.products[spec.sku],
            category_id=None if spec.category is None else self.categories[spec.category],
            customer_tier=spec.customer_tier,
            valid_from=day(-GO_LIVE_DAYS if spec.starts is None else spec.starts),
            valid_to=None if spec.ends is None else day(spec.ends),
        )

    def report(self) -> SeededTenant:
        spec = self.spec
        return SeededTenant(
            name=spec.name,
            tenant_id=self.admin.tenant_id,
            quote_prefix=spec.quote_prefix,
            order_prefix=spec.order_prefix,
            people=(spec.admin, *spec.people),
            counts={
                "users": len(self.people),
                "categories": len(self.categories),
                "products": len(self.products),
                "customers": len(self.customers),
                "pricing rules": self.rules,
            },
        )


async def seed_demo(
    *,
    unit_of_work: UnitOfWorkFactory,
    hasher: PasswordHasher,
    as_of: date,
    seed: int = DEFAULT_SEED,
    password: str = DEMO_PASSWORD,
) -> SeedReport:
    """Load every demo tenant as of ``as_of``.

    Raise ``EmailAlreadyRegisteredError``, having changed nothing, when Northfield is already
    loaded: demo data goes into a database without it (``just seed`` resets the local one).
    """
    seeded = []
    for spec in demo_tenants(seed):
        go_live = datetime.combine(as_of - timedelta(days=GO_LIVE_DAYS), GO_LIVE_HOUR)
        loader = TenantLoader(spec, unit_of_work, DemoClock(go_live))
        await loader.register(hasher, password)
        await loader.load_catalog()
        await loader.load_customers()
        await loader.load_rules(as_of)
        seeded.append(loader.report())
    return SeedReport(as_of=as_of, seed=seed, tenants=tuple(seeded))
