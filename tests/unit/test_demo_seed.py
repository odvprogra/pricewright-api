"""Loading the demo tenants through the use cases (ADR-0024), on the fakes.

The full demo is loaded once for the module (seconds on the fakes); other tests load Larkspur
alone, or a plan with one story of each kind.
"""

import asyncio
import dataclasses
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pricewright.application.ports import UnitOfWork
from pricewright.application.quotes import list_approval_requests
from pricewright.demo.catalog import northfield_catalog
from pricewright.demo.seed import (
    DEMO_PASSWORD,
    GO_LIVE_DAYS,
    SeededTenant,
    SeedReport,
    load_tenant,
    seed_demo,
)
from pricewright.demo.tenants import DemoTenant, StoryKind, demo_tenants
from pricewright.domain.audit import AuditAction
from pricewright.domain.auth import Principal
from pricewright.domain.orders import OrderStatus
from pricewright.domain.pricing_rules import RuleKind
from pricewright.domain.quote_approvals import ApprovalStatus
from pricewright.domain.quote_lifecycle import EXPIRABLE, QuoteStatus
from pricewright.domain.users import EmailAlreadyRegisteredError, Role
from tests.demo_invariants import check_demo
from tests.demo_snapshot import Picture, picture
from tests.fakes import FakeClock, FakePasswordHasher, FakeUnitOfWork, InMemoryDatabase

AS_OF = date(2026, 10, 1)
MIDNIGHT = datetime.combine(AS_OF, time(tzinfo=UTC))
GO_LIVE = datetime(2026, 3, 25, 14, tzinfo=UTC)  # AS_OF minus GO_LIVE_DAYS, at 14:00 UTC
type Seeded = tuple[InMemoryDatabase, SeedReport]


def _units(database: InMemoryDatabase) -> FakeUnitOfWork:
    return FakeUnitOfWork(database)


async def _seed(database: InMemoryDatabase, *, as_of: date = AS_OF, seed: int = 7) -> SeedReport:
    def unit_of_work() -> UnitOfWork:
        return _units(database)

    return await seed_demo(
        unit_of_work=unit_of_work, hasher=FakePasswordHasher(), as_of=as_of, seed=seed
    )


async def _load(
    spec: DemoTenant, database: InMemoryDatabase, *, as_of: date, seed: int
) -> SeededTenant:
    def unit_of_work() -> UnitOfWork:
        return _units(database)

    return await load_tenant(
        spec,
        unit_of_work=unit_of_work,
        hasher=FakePasswordHasher(),
        as_of=as_of,
        seed=seed,
        password=DEMO_PASSWORD,
    )


async def _picture(database: InMemoryDatabase, *tenants: SeededTenant) -> list[Picture]:
    def unit_of_work() -> UnitOfWork:
        return _units(database)

    return await picture(unit_of_work, [tenant.tenant_id for tenant in tenants])


async def _check(database: InMemoryDatabase, as_of: date, *tenants: SeededTenant) -> None:
    def unit_of_work() -> UnitOfWork:
        return _units(database)

    await check_demo(unit_of_work, [tenant.tenant_id for tenant in tenants], as_of)


@pytest.fixture(scope="module")
def seeded() -> Seeded:
    """Loaded once for the tests that only read it."""
    database = InMemoryDatabase()
    return database, asyncio.run(_seed(database))


def _northfield_quotes(seeded: Seeded) -> list[QuoteStatus]:
    database, report = seeded
    tenant_id = report.tenants[0].tenant_id
    return [q.status for q in database.quotes.values() if q.tenant_id == tenant_id]


def test_go_live_is_go_live_days_before_the_as_of_date() -> None:
    assert AS_OF - GO_LIVE.date() == timedelta(days=GO_LIVE_DAYS)


def test_seed_demo_loads_both_tenants_with_their_prefixes_and_counts(seeded: Seeded) -> None:
    _, report = seeded

    northfield, larkspur = report.tenants

    assert (report.as_of, report.seed) == (AS_OF, 7)
    assert (northfield.name, northfield.quote_prefix, northfield.order_prefix) == (
        "Northfield Supply",
        "NF",
        "NFO",
    )
    assert dict(northfield.counts) == {
        "users": 5,
        "categories": 8,
        "products": 300,
        "customers": 80,
        "pricing rules": 13,
        "quotes": 150,
        "revisions": 11,
        "orders": 69,
    }
    assert (larkspur.name, larkspur.quote_prefix, larkspur.order_prefix) == (
        "Larkspur Tool Co.",
        "LT",
        "LTO",
    )
    assert dict(larkspur.counts) == {
        "users": 2,
        "categories": 4,
        "products": 19,
        "customers": 8,
        "pricing rules": 3,
        "quotes": 10,
        "revisions": 0,
        "orders": 4,
    }


def test_seed_demo_keeps_every_business_rule_in_its_data(seeded: Seeded) -> None:
    database, report = seeded

    asyncio.run(_check(database, AS_OF, *report.tenants))  # assertions in tests/demo_invariants


def test_seed_demo_leaves_quotes_in_every_state_of_their_lifecycle(seeded: Seeded) -> None:
    statuses = Counter(_northfield_quotes(seeded))

    assert statuses == {
        QuoteStatus.CONVERTED: 69,
        QuoteStatus.SUPERSEDED: 11,  # revised: rejected, changed, expired
        QuoteStatus.SENT: 25,
        QuoteStatus.APPROVED: 8,
        QuoteStatus.PENDING_APPROVAL: 10,
        QuoteStatus.ACCEPTED: 5,
        QuoteStatus.REJECTED: 5,
        QuoteStatus.CANCELLED: 16,
        QuoteStatus.DRAFT: 12,
    }


def test_seed_demo_leaves_twelve_offers_expired_on_the_as_of_date(seeded: Seeded) -> None:
    database, report = seeded
    tenant_id = report.tenants[0].tenant_id
    in_flight = [
        q for q in database.quotes.values() if q.tenant_id == tenant_id and q.status in EXPIRABLE
    ]

    expired = [q for q in in_flight if q.valid_until < AS_OF]

    assert len(expired) == 12  # sent, approved and pending offers nobody answered
    assert all(q.valid_until >= AS_OF for q in in_flight if q not in expired)


def test_seed_demo_fills_the_approval_inbox_for_the_as_of_date(seeded: Seeded) -> None:
    database, report = seeded
    northfield = report.tenants[0]
    manager = next(u for u in database.users.values() if u.role is Role.SALES_MANAGER)

    inbox = asyncio.run(
        list_approval_requests(
            Principal(northfield.tenant_id, manager.id, Role.SALES_MANAGER),
            ApprovalStatus.PENDING,
            after=None,
            limit=50,
            unit_of_work=lambda: _units(database),
            clock=FakeClock(MIDNIGHT + timedelta(hours=15)),
        )
    )

    assert len(inbox.items) == 8  # the two expired requests left it


def test_seed_demo_places_orders_and_cancels_a_few(seeded: Seeded) -> None:
    database, report = seeded
    tenant_id = report.tenants[0].tenant_id

    orders = Counter(o.status for o in database.orders.values() if o.tenant_id == tenant_id)

    assert orders == {OrderStatus.OPEN: 64, OrderStatus.CANCELLED: 5}


def test_seed_demo_has_managers_approve_reps_and_admins_approve_managers(seeded: Seeded) -> None:
    database, _ = seeded
    roles = {user.id: user.role for user in database.users.values()}

    deciders = Counter(
        (roles[q.created_by.id], roles[request.decided_by])
        for q in database.quotes.values()
        for request in q.approvals
        if request.decided_by is not None
        and q.tenant_id == next(iter(database.tenants))  # Northfield, registered first
    )

    assert set(deciders) <= {
        (Role.SALES_REP, Role.SALES_MANAGER),
        (Role.SALES_REP, Role.ADMIN),  # the manager overrode a price on it
        (Role.SALES_MANAGER, Role.ADMIN),
    }
    assert deciders[Role.SALES_MANAGER, Role.ADMIN] == 2


def test_seed_demo_gives_every_person_their_role_and_the_demo_passphrase(seeded: Seeded) -> None:
    database, report = seeded
    northfield = report.tenants[0].tenant_id

    roles = Counter(user.role for user in database.users.values() if user.tenant_id == northfield)

    assert roles == {Role.ADMIN: 1, Role.SALES_MANAGER: 1, Role.SALES_REP: 3}
    hashes = {user.password_hash for user in database.users.values()}
    assert hashes == {FakePasswordHasher.PREFIX + DEMO_PASSWORD}


def test_seed_demo_sells_the_committed_catalog_at_its_prices(seeded: Seeded) -> None:
    database, report = seeded
    northfield = report.tenants[0].tenant_id
    products = {p.sku: p for p in database.products.values() if p.tenant_id == northfield}

    catalog = northfield_catalog()

    assert products.keys() == {item.sku for item in catalog}
    assert all(
        (products[item.sku].list_price.amount, products[item.sku].unit_cost.amount)
        == (item.list_price, item.unit_cost)
        for item in catalog
    )


def test_seed_demo_enters_master_data_on_go_live_day_by_the_right_roles(seeded: Seeded) -> None:
    database, _ = seeded
    roles = {user.id: user.role for user in database.users.values()}
    master = [e for e in database.audit_events.values() if e.occurred_at.date() == GO_LIVE.date()]

    creators = {(event.action, roles[event.actor_id]) for event in master}

    assert min(event.occurred_at for event in database.audit_events.values()) == GO_LIVE
    assert creators == {
        (AuditAction.USER_CREATED, Role.ADMIN),
        (AuditAction.PRODUCT_CATEGORY_CREATED, Role.ADMIN),
        (AuditAction.PRODUCT_CREATED, Role.ADMIN),
        (AuditAction.CUSTOMER_CREATED, Role.SALES_REP),
        (AuditAction.PRICING_RULE_CREATED, Role.SALES_MANAGER),  # Northfield's manager
        (AuditAction.PRICING_RULE_CREATED, Role.ADMIN),  # Larkspur has no manager
    }


def test_seed_demo_shares_each_tenants_customers_among_its_reps(seeded: Seeded) -> None:
    database, report = seeded
    northfield = report.tenants[0].tenant_id
    reps = {u.id for u in database.users.values() if u.tenant_id == northfield}

    created_by = Counter(
        event.actor_id
        for event in database.audit_events.values()
        if event.action is AuditAction.CUSTOMER_CREATED and event.tenant_id == northfield
    )

    assert set(created_by) <= reps
    assert sorted(created_by.values()) == [26, 27, 27]


def test_seed_demo_dates_pricing_rules_from_the_as_of_date(seeded: Seeded) -> None:
    database, report = seeded
    northfield = report.tenants[0].tenant_id
    rules = [r for r in database.pricing_rules.values() if r.tenant_id == northfield]

    promotions = sorted(
        (r.valid_from - MIDNIGHT, r.valid_to - MIDNIGHT)
        for r in rules
        if r.kind is RuleKind.PROMOTION and r.valid_to is not None
    )

    assert promotions == [
        (timedelta(days=-150), timedelta(days=-136)),
        (timedelta(days=-80), timedelta(days=-50)),
        (timedelta(days=-21), timedelta(days=24)),  # running on the as-of date
        (timedelta(days=30), timedelta(days=61)),  # not started yet
    ]
    standing = [r for r in rules if r.kind is not RuleKind.PROMOTION]
    assert {(r.valid_from, r.valid_to) for r in standing} == {(MIDNIGHT - timedelta(190), None)}


def test_seed_demo_gives_the_same_data_for_the_same_seed_and_date(seeded: Seeded) -> None:
    database, report = seeded
    again = InMemoryDatabase()

    repeated = asyncio.run(_seed(again))

    first = asyncio.run(_picture(database, *report.tenants))
    assert asyncio.run(_picture(again, *repeated.tenants)) == first


async def test_seed_demo_tells_another_story_with_another_seed() -> None:
    larkspur = demo_tenants(7)[1]
    first, other = InMemoryDatabase(), InMemoryDatabase()

    pictures = [
        await _picture(first, await _load(larkspur, first, as_of=AS_OF, seed=7)),
        await _picture(other, await _load(demo_tenants(8)[1], other, as_of=AS_OF, seed=8)),
    ]

    assert pictures[0][0]["customers"] != pictures[1][0]["customers"]
    assert pictures[0][0]["quotes"] != pictures[1][0]["quotes"]


async def test_seed_demo_moves_with_the_as_of_date() -> None:
    database = InMemoryDatabase()

    loaded = await _load(demo_tenants(7)[1], database, as_of=date(2027, 2, 14), seed=7)

    events = database.audit_events.values()
    assert min(event.occurred_at for event in events) == datetime(2026, 8, 8, 14, tzinfo=UTC)
    assert max(event.occurred_at for event in events) < datetime(2027, 2, 14, tzinfo=UTC)
    await _check(database, date(2027, 2, 14), loaded)


def test_seed_demo_refuses_to_load_twice_and_changes_nothing(seeded: Seeded) -> None:
    database, report = seeded
    before = asyncio.run(_picture(database, *report.tenants))

    with pytest.raises(EmailAlreadyRegisteredError):
        asyncio.run(_seed(database))

    assert asyncio.run(_picture(database, *report.tenants)) == before


def _one_of_each(spec: DemoTenant) -> DemoTenant:
    plan = dataclasses.replace(spec.stories, counts=dict.fromkeys(StoryKind, 1))
    return dataclasses.replace(spec, stories=plan)


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=0, max_value=2**32),
    as_of=st.dates(min_value=date(2025, 1, 1), max_value=date(2031, 12, 31)),
)
async def test_any_seed_and_date_tell_every_kind_of_story_within_the_rules(
    seed: int, as_of: date
) -> None:
    database = InMemoryDatabase()
    spec = _one_of_each(demo_tenants(seed)[0])

    loaded = await _load(spec, database, as_of=as_of, seed=seed)

    assert loaded.counts["quotes"] == len(StoryKind)
    await _check(database, as_of, loaded)
