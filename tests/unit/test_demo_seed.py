"""Loading the demo tenants' master data through the use cases (ADR-0024), on the fakes."""

import asyncio
from collections import Counter
from datetime import UTC, date, datetime, timedelta

import pytest

from pricewright.application.ports import UnitOfWork
from pricewright.demo.catalog import northfield_catalog
from pricewright.demo.seed import DEMO_PASSWORD, GO_LIVE_DAYS, SeedReport, seed_demo
from pricewright.domain.audit import AuditAction
from pricewright.domain.pricing_rules import RuleKind
from pricewright.domain.users import EmailAlreadyRegisteredError, Role
from tests.demo_snapshot import Picture, picture
from tests.fakes import FakePasswordHasher, FakeUnitOfWork, InMemoryDatabase

AS_OF = date(2026, 10, 1)
GO_LIVE = datetime(2026, 3, 25, 14, tzinfo=UTC)  # AS_OF minus GO_LIVE_DAYS, at 14:00 UTC


async def _seed(database: InMemoryDatabase, *, as_of: date = AS_OF, seed: int = 7) -> SeedReport:
    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(database)

    return await seed_demo(
        unit_of_work=unit_of_work, hasher=FakePasswordHasher(), as_of=as_of, seed=seed
    )


async def _picture(database: InMemoryDatabase, report: SeedReport) -> list[Picture]:
    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(database)

    return await picture(unit_of_work, [tenant.tenant_id for tenant in report.tenants])


@pytest.fixture(scope="module")
def seeded() -> tuple[InMemoryDatabase, SeedReport]:
    """Loaded once for the tests that only read it."""
    database = InMemoryDatabase()
    return database, asyncio.run(_seed(database))


def test_go_live_is_go_live_days_before_the_as_of_date() -> None:
    assert AS_OF - GO_LIVE.date() == timedelta(days=GO_LIVE_DAYS)


async def test_seed_demo_loads_both_tenants_with_their_prefixes_and_counts(
    seeded: tuple[InMemoryDatabase, SeedReport],
) -> None:
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
    }


async def test_seed_demo_gives_every_person_their_role_and_the_demo_passphrase(
    seeded: tuple[InMemoryDatabase, SeedReport],
) -> None:
    database, report = seeded
    northfield = report.tenants[0].tenant_id

    roles = Counter(user.role for user in database.users.values() if user.tenant_id == northfield)

    assert roles == {Role.ADMIN: 1, Role.SALES_MANAGER: 1, Role.SALES_REP: 3}
    hashes = {user.password_hash for user in database.users.values()}
    assert hashes == {FakePasswordHasher.PREFIX + DEMO_PASSWORD}


async def test_seed_demo_sells_the_committed_catalog_at_its_prices(
    seeded: tuple[InMemoryDatabase, SeedReport],
) -> None:
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


async def test_seed_demo_records_who_created_what_on_go_live_day(
    seeded: tuple[InMemoryDatabase, SeedReport],
) -> None:
    database, _ = seeded
    events = database.audit_events.values()
    roles = {user.id: user.role for user in database.users.values()}

    creators = {(event.action, roles[event.actor_id]) for event in events}

    assert {event.occurred_at.date() for event in events} == {GO_LIVE.date()}
    assert min(event.occurred_at for event in events) == GO_LIVE
    assert creators == {
        (AuditAction.USER_CREATED, Role.ADMIN),
        (AuditAction.PRODUCT_CATEGORY_CREATED, Role.ADMIN),
        (AuditAction.PRODUCT_CREATED, Role.ADMIN),
        (AuditAction.CUSTOMER_CREATED, Role.SALES_REP),
        (AuditAction.PRICING_RULE_CREATED, Role.SALES_MANAGER),  # Northfield's manager
        (AuditAction.PRICING_RULE_CREATED, Role.ADMIN),  # Larkspur has no manager
    }


async def test_seed_demo_shares_each_tenants_customers_among_its_reps(
    seeded: tuple[InMemoryDatabase, SeedReport],
) -> None:
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


async def test_seed_demo_dates_pricing_rules_from_the_as_of_date(
    seeded: tuple[InMemoryDatabase, SeedReport],
) -> None:
    database, report = seeded
    northfield = report.tenants[0].tenant_id
    rules = [r for r in database.pricing_rules.values() if r.tenant_id == northfield]
    midnight = datetime(2026, 10, 1, tzinfo=UTC)

    promotions = sorted(
        (r.valid_from - midnight, r.valid_to - midnight)
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
    assert {(r.valid_from, r.valid_to) for r in standing} == {(midnight - timedelta(190), None)}


async def test_seed_demo_gives_the_same_data_for_the_same_seed_and_date() -> None:
    first, second, other_seed = InMemoryDatabase(), InMemoryDatabase(), InMemoryDatabase()

    pictures = [
        await _picture(first, await _seed(first)),
        await _picture(second, await _seed(second)),
        await _picture(other_seed, await _seed(other_seed, seed=8)),
    ]

    assert pictures[0] == pictures[1]
    assert pictures[0] != pictures[2]
    assert [t["customers"] for t in pictures[0]] != [t["customers"] for t in pictures[2]]


async def test_seed_demo_moves_with_the_as_of_date() -> None:
    database = InMemoryDatabase()

    await _seed(database, as_of=date(2027, 2, 14))

    first_event = min(event.occurred_at for event in database.audit_events.values())
    assert first_event == datetime(2026, 8, 8, 14, tzinfo=UTC)  # 190 days before


async def test_seed_demo_refuses_to_load_twice_and_changes_nothing() -> None:
    database = InMemoryDatabase()
    report = await _seed(database)
    before = await _picture(database, report)

    with pytest.raises(EmailAlreadyRegisteredError):
        await _seed(database)

    assert await _picture(database, report) == before
    assert len(database.tenants) == 2
