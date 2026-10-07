"""A canonical picture of the demo data, read through the ports, to compare two loads (ADR-0024).

Ids are UUIDv7 drawn from the real clock, and some timestamps are set by the database, so the
picture leaves both out: every id becomes its record's business key (an email, a SKU, an account
number, a category or rule name). Two loads with the same seed and date give equal pictures, on
the fakes and on PostgreSQL alike.
"""

from collections.abc import Callable, Mapping, Sequence
from uuid import UUID

from pricewright.application.ports import (
    AuditEventFilter,
    CustomerQuery,
    CustomerSort,
    OrderQuery,
    PricingRuleQuery,
    PricingRuleSort,
    ProductQuery,
    ProductSort,
    QuoteQuery,
    UnitOfWorkFactory,
)
from pricewright.domain.audit import AuditValue
from pricewright.domain.orders import Order
from pricewright.domain.pricing import PricedLine
from pricewright.domain.quotes import Quote

LIMIT = 100_000
type Picture = dict[str, object]
type Key = Callable[[UUID | None], str | None]


def _priced(line: PricedLine, key: Key) -> tuple[object, ...]:
    breakdown, floor = line.breakdown, line.margin_floor
    steps = tuple(
        (step.stage, step.label, key(step.rule_id), step.rate, step.amount, step.unit_price)
        for step in breakdown.steps
    )
    return (
        (key(line.product_id), line.quantity, breakdown.list_unit_price, steps),
        (line.list_total, line.net_total, line.cost_total),
        None if floor is None else (key(floor.rule_id), floor.label, floor.rate),
    )


def _quote(quote: Quote, key: Key) -> tuple[object, ...]:
    totals, submitted = quote.totals, quote.submitted_by
    lines = [
        (
            (line.sku, line.product_name, line.unit, line.quantity, key(line.added_by.id)),
            (_priced(line.pricing, key), line.override, key(line.override_by)),
        )
        for line in quote.lines
    ]
    approvals = [
        (
            (a.requested_at, key(a.requested_by.id), a.reasons, a.discount, a.approval_threshold),
            (a.list_subtotal, a.net_subtotal, a.status, key(a.decided_by), a.decided_at, a.comment),
        )
        for a in quote.approvals
    ]
    return (
        (quote.display_number, key(quote.customer_id), quote.status, quote.valid_until),
        (quote.created_at, key(quote.created_by.id), quote.status_changed_at, quote.notes),
        (quote.submitted_at, None if submitted is None else key(submitted.id)),
        (totals.list_subtotal, totals.net_subtotal, totals.tax_rate, totals.tax, totals.total),
        (totals.approval_threshold, totals.priced_at, quote.cancel_reason, quote.version),
        (key(quote.supersedes_id), key(quote.superseded_by_id), key(quote.order_id)),
        lines,
        approvals,
    )


def _order(order: Order, key: Key) -> tuple[object, ...]:
    totals, customer = order.totals, order.customer
    lines = [
        (
            (line.sku, line.product_name, line.unit, line.quantity, _priced(line.pricing, key)),
            (line.override, key(line.override_by)),
        )
        for line in order.lines
    ]
    return (
        (order.number, key(order.quote_id), order.quote_number, order.customer_reference),
        (key(customer.id), customer.name, customer.tax_id, customer.payment_terms_days),
        (totals.list_subtotal, totals.net_subtotal, totals.tax_rate, totals.tax, totals.total),
        (totals.priced_at, order.created_at, key(order.created_by.id)),
        (order.status, order.status_changed_at, order.cancel_reason, order.version),
        lines,
    )


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
        oldest_first = QuoteQuery(descending=False)
        summaries = await uow.quotes.page(oldest_first, after=None, limit=LIMIT)
        quotes = [await uow.quotes.get(summary.id) for summary in summaries]
        placed = await uow.orders.page(OrderQuery(descending=False), after=None, limit=LIMIT)
        orders = [await uow.orders.get(summary.id) for summary in placed]
        events = await uow.audit_events.page(AuditEventFilter(), before=None, limit=LIMIT)
    assert tenant is not None
    stored_quotes = [quote for quote in quotes if quote is not None]
    stored_orders = [order for order in orders if order is not None]
    keys: dict[UUID, str] = {
        **{user.id: user.email for user in users},
        **{category.id: category.name for category in categories},
        **{product.id: product.sku for product in products},
        **{customer.id: customer.account_number for customer in customers},
        **{rule.id: rule.name for rule in rules},
        **{quote.id: quote.display_number for quote in stored_quotes},
        **{
            line.id: f"{quote.display_number} line {position}"
            for quote in stored_quotes
            for position, line in enumerate(quote.lines, 1)
        },
        **{order.id: order.number for order in stored_orders},
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
        "quotes": [_quote(quote, key) for quote in stored_quotes],
        "orders": [_order(order, key) for order in stored_orders],
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
