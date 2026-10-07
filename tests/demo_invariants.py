"""The business rules that seeded demo data must keep, read back through the ports (ADR-0024).

They hold for any seed and as-of date, on the fakes and on PostgreSQL: totals reconcile, four eyes
decide every approval, nothing that needed approval went out without one, every converted quote has
exactly one order with its prices, numbers have no gaps, revisions are linked, nothing points to
another tenant, and nothing happens on or after the as-of date.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID

from pricewright.application.ports import (
    AuditEventFilter,
    CustomerQuery,
    OrderQuery,
    ProductQuery,
    QuoteQuery,
    UnitOfWorkFactory,
)
from pricewright.domain.audit import AuditEvent
from pricewright.domain.money import Money, round_half_up
from pricewright.domain.orders import Order
from pricewright.domain.pricing import PricedLine
from pricewright.domain.quote_approvals import ApprovalStatus
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.quotes import Quote
from pricewright.domain.tenants import Tenant
from pricewright.domain.users import Role, User

LIMIT = 100_000
OUT_WITH_APPROVAL = {
    QuoteStatus.APPROVED,
    QuoteStatus.SENT,
    QuoteStatus.ACCEPTED,
    QuoteStatus.CONVERTED,
}


@dataclass(frozen=True, slots=True)
class TenantData:
    tenant: Tenant
    users: dict[UUID, User]
    customers: set[UUID]
    products: set[UUID]
    quotes: list[Quote]
    orders: list[Order]
    events: list[AuditEvent]


async def load(unit_of_work: UnitOfWorkFactory, tenant_id: UUID) -> TenantData:
    async with unit_of_work() as uow:
        uow.bind_tenant(tenant_id)
        tenant = await uow.tenants.get(tenant_id)
        users = await uow.users.page(after=None, limit=LIMIT)
        customers = await uow.customers.page(CustomerQuery(), after=None, limit=LIMIT)
        products = await uow.products.page(ProductQuery(), after=None, limit=LIMIT)
        summaries = await uow.quotes.page(QuoteQuery(descending=False), after=None, limit=LIMIT)
        quotes = [await uow.quotes.get(summary.id) for summary in summaries]
        placed = await uow.orders.page(OrderQuery(descending=False), after=None, limit=LIMIT)
        orders = [await uow.orders.get(summary.id) for summary in placed]
        events = await uow.audit_events.page(AuditEventFilter(), before=None, limit=LIMIT)
    assert tenant is not None
    return TenantData(
        tenant=tenant,
        users={user.id: user for user in users},
        customers={customer.id for customer in customers},
        products={product.id for product in products},
        quotes=[quote for quote in quotes if quote is not None],
        orders=[order for order in orders if order is not None],
        events=events,
    )


def _cents(amount: Decimal, like: Money) -> Money:
    return Money(round_half_up(amount, like.minor_units), like.currency)


def _reconciles(lines: Iterable[PricedLine], net: Money, tax_rate: Decimal, tax: Money) -> bool:
    """Line totals rounded once, summed, and tax computed once on the sum (ADR-0003)."""
    priced = list(lines)
    line_totals_add_up = sum(line.net_total.amount for line in priced) == net.amount
    each_line_rounded = all(
        line.net_total == _cents(line.breakdown.net_unit_price.amount * line.quantity, net)
        for line in priced
    )
    return line_totals_add_up and each_line_rounded and tax == _cents(net.amount * tax_rate, net)


def check_totals(data: TenantData) -> None:
    for quote in data.quotes:
        totals = quote.totals
        lines = [line.pricing for line in quote.lines]
        assert _reconciles(lines, totals.net_subtotal, totals.tax_rate, totals.tax), quote.number
        assert totals.total == totals.net_subtotal + totals.tax
        assert totals.list_subtotal.amount == sum(line.list_total.amount for line in lines)
    for order in data.orders:
        placed = order.totals
        priced = [line.pricing for line in order.lines]
        assert _reconciles(priced, placed.net_subtotal, placed.tax_rate, placed.tax), order.number
        assert placed.total == placed.net_subtotal + placed.tax


def _builders(quote: Quote) -> set[UUID]:
    """Who built the revision (ADR-0020): creator, submitters, line authors, override setters."""
    people = {quote.created_by.id, *(a.requested_by.id for a in quote.approvals)}
    people |= {line.added_by.id for line in quote.lines}
    return people | {line.override_by for line in quote.lines if line.override_by is not None}


def check_approvals(data: TenantData) -> None:
    deciders = {Role.SALES_MANAGER, Role.ADMIN}
    for quote in data.quotes:
        for request in quote.approvals:
            if request.decided_by is not None:
                assert data.users[request.decided_by].role in deciders
                assert request.decided_by not in _builders(quote), f"self-approval {quote.number}"
        if quote.status in OUT_WITH_APPROVAL and quote.approval_reasons:
            statuses = [request.status for request in quote.approvals]
            assert ApprovalStatus.APPROVED in statuses, f"{quote.display_number} skipped approval"


def check_orders(data: TenantData) -> None:
    converted = {q.id: q for q in data.quotes if q.status is QuoteStatus.CONVERTED}
    by_quote = {order.quote_id: order for order in data.orders}
    assert len(by_quote) == len(data.orders), "a quote with two orders"
    assert by_quote.keys() == converted.keys(), "orders and converted quotes differ"
    for quote_id, order in by_quote.items():
        quote = converted[quote_id]
        assert quote.order_id == order.id
        assert order.quote_number == quote.display_number
        assert [line.pricing for line in order.lines] == [line.pricing for line in quote.lines]
        assert order.totals.net_subtotal == quote.totals.net_subtotal
        assert order.totals.total == quote.totals.total


def _gapless(numbers: Iterable[str], prefix: str) -> bool:
    by_year: dict[str, list[int]] = defaultdict(list)
    for number in numbers:
        start, year, sequence = number.split("-")
        assert start == prefix
        by_year[year].append(int(sequence))
    return all(sorted(seq) == list(range(1, len(seq) + 1)) for seq in by_year.values())


def check_numbers(data: TenantData) -> None:
    settings = data.tenant.settings
    firsts = [quote.number for quote in data.quotes if quote.revision == 1]
    assert _gapless(firsts, settings.quote_prefix)
    assert _gapless((order.number for order in data.orders), settings.order_prefix)


def check_revisions(data: TenantData) -> None:
    quotes = {quote.id: quote for quote in data.quotes}
    for quote in data.quotes:
        if quote.superseded_by_id is not None:
            successor = quotes[quote.superseded_by_id]
            assert quote.status is QuoteStatus.SUPERSEDED
            assert (successor.number, successor.revision) == (quote.number, quote.revision + 1)
            assert successor.supersedes_id == quote.id
        if quote.revision > 1:
            assert quote.supersedes_id in quotes


def check_isolation(data: TenantData) -> None:
    """Every record a tenant's documents point to is one of its own."""
    for quote in data.quotes:
        assert quote.customer_id in data.customers
        assert {line.product_id for line in quote.lines} <= data.products
        assert _builders(quote) <= data.users.keys()
    for order in data.orders:
        assert order.customer.id in data.customers
        assert {line.product_id for line in order.lines} <= data.products
        assert order.created_by.id in data.users
    assert {event.actor_id for event in data.events} <= data.users.keys()


def check_before(data: TenantData, as_of: date) -> None:
    midnight = datetime.combine(as_of, time(tzinfo=UTC))
    assert all(event.occurred_at < midnight for event in data.events)
    assert all(quote.created_at < midnight for quote in data.quotes)


async def check_demo(
    unit_of_work: UnitOfWorkFactory, tenant_ids: Iterable[UUID], as_of: date
) -> None:
    for tenant_id in tenant_ids:
        data = await load(unit_of_work, tenant_id)
        check_totals(data)
        check_approvals(data)
        check_orders(data)
        check_numbers(data)
        check_revisions(data)
        check_isolation(data)
        check_before(data, as_of)
