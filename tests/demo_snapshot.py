"""A canonical picture of the demo data, read through the ports, to compare two loads (ADR-0024).

Ids are UUIDv7 drawn from the real clock, and some timestamps are set by the database, so the
picture leaves both out: every id becomes its record's business key (an email, a SKU, an account
number, a category or rule name). Two loads with the same seed and date give equal pictures, on
the fakes and on PostgreSQL alike.
"""

from collections.abc import Mapping, Sequence
from uuid import UUID

from pricewright.application.ports import (
    AuditEventFilter,
    CustomerQuery,
    CustomerSort,
    PricingRuleQuery,
    PricingRuleSort,
    ProductQuery,
    ProductSort,
    UnitOfWorkFactory,
)
from pricewright.domain.audit import AuditValue

LIMIT = 100_000
type Picture = dict[str, object]


def _named(value: AuditValue, keys: Mapping[UUID, str]) -> object:
    """An id inside an audit change becomes the key of the record it names."""
    if isinstance(value, str) and len(value) == 36:
        try:
            return keys.get(UUID(value), value)
        except ValueError:
            return value
    return value


async def _tenant(unit_of_work: UnitOfWorkFactory, tenant_id: UUID) -> Picture:
    async with unit_of_work() as uow:
        uow.bind_tenant(tenant_id)
        tenant = await uow.tenants.get(tenant_id)
        users = await uow.users.page(after=None, limit=LIMIT)
        categories = await uow.product_categories.page(after=None, limit=LIMIT)
        by_sku = ProductQuery(sort=ProductSort.SKU)
        products = await uow.products.page(by_sku, after=None, limit=LIMIT)
        by_account = CustomerQuery(sort=CustomerSort.ACCOUNT_NUMBER)
        customers = await uow.customers.page(by_account, after=None, limit=LIMIT)
        by_name = PricingRuleQuery(sort=PricingRuleSort.NAME)
        rules = await uow.pricing_rules.page(by_name, after=None, limit=LIMIT)
        events = await uow.audit_events.page(AuditEventFilter(), before=None, limit=LIMIT)
    assert tenant is not None
    keys: dict[UUID, str] = {
        **{user.id: user.email for user in users},
        **{category.id: category.name for category in categories},
        **{product.id: product.sku for product in products},
        **{customer.id: customer.account_number for customer in customers},
        **{rule.id: rule.name for rule in rules},
    }

    def key(record_id: UUID | None) -> str | None:
        return None if record_id is None else keys[record_id]

    return {
        "tenant": (tenant.name, tenant.settings),
        "users": sorted((u.email, u.full_name, u.role, u.is_active) for u in users),
        "categories": sorted(category.name for category in categories),
        "products": [
            (p.sku, p.name, key(p.category_id), p.unit, p.list_price, p.unit_cost, p.is_active)
            for p in products
        ],
        "customers": [
            (c.account_number, c.name, c.tier, c.payment_terms_days, c.tax_id, c.is_active)
            for c in customers
        ],
        "rules": [
            (
                (rule.kind, rule.name, rule.rate, rule.brackets, rule.customer_tier),
                (key(rule.product_id), key(rule.category_id)),
                (rule.valid_from, rule.valid_to, rule.is_active),
            )
            for rule in rules
        ],
        "audit": [
            (
                event.occurred_at,
                event.action,
                key(event.actor_id),
                key(event.resource_id),
                {
                    field: (_named(before, keys), _named(after, keys))
                    for field, (before, after) in event.changes.items()
                },
            )
            for event in reversed(events)  # oldest first
        ],
    }


async def picture(unit_of_work: UnitOfWorkFactory, tenant_ids: Sequence[UUID]) -> list[Picture]:
    """Every tenant's picture, in the order given."""
    return [await _tenant(unit_of_work, tenant_id) for tenant_id in tenant_ids]
