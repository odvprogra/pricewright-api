"""The demo tenants' data (ADR-0024): seeded draws, customers and the tenants' specifications."""

import uuid
from collections import Counter

from hypothesis import given
from hypothesis import strategies as st

from pricewright.demo.customers import demo_customers
from pricewright.demo.randomness import between, chance, generator, pick, shuffled
from pricewright.demo.tenants import DemoTenant, demo_tenants
from pricewright.domain.customers import Customer, CustomerTier
from pricewright.domain.users import Role

seeds = st.integers(min_value=0, max_value=2**32)
TIERS = {CustomerTier.GOLD: 4, CustomerTier.SILVER: 7, CustomerTier.STANDARD: 19}


@given(seed=seeds)
def test_generator_repeats_its_draws_for_the_same_seed_and_purpose(seed: int) -> None:
    first, again = generator(seed, "customers"), generator(seed, "customers")
    other = generator(seed, "quotes")

    draws = [first.random() for _ in range(5)]

    assert draws == [again.random() for _ in range(5)]
    assert draws != [other.random() for _ in range(5)]


def test_generator_keeps_the_sequence_python_promises_across_versions() -> None:
    """If Python changed its seeding, every demo record would change: pinned here."""
    assert generator(2026, "pinned").random() == 0.09105328764420217


@given(seed=seeds, low=st.integers(-50, 50), width=st.integers(0, 50))
def test_between_stays_within_both_bounds(seed: int, low: int, width: int) -> None:
    rng = generator(seed, "between")

    draws = [between(rng, low, low + width) for _ in range(20)]

    assert all(low <= draw <= low + width for draw in draws)


@given(seed=seeds, items=st.lists(st.integers(), max_size=30))
def test_shuffled_returns_a_permutation_and_leaves_the_input_alone(
    seed: int, items: list[int]
) -> None:
    original = list(items)

    result = shuffled(generator(seed, "shuffle"), items)

    assert sorted(result) == sorted(original)
    assert items == original


def test_pick_and_chance_cover_their_range() -> None:
    rng = generator(1, "coverage")

    picks = Counter(pick(rng, "abc") for _ in range(300))
    hits = sum(chance(rng, 0.25) for _ in range(1000))

    assert set(picks) == {"a", "b", "c"}
    assert 150 < hits < 350
    assert not chance(rng, 0.0)


@given(seed=seeds)
def test_demo_customers_are_valid_unique_and_split_by_tier(seed: int) -> None:
    customers = demo_customers(generator(seed, "test"), account_prefix="C-", tiers=TIERS)

    stored = [
        Customer.create(
            tenant_id=uuid.uuid7(),
            account_number=new.account_number,
            name=new.name,
            tier=new.tier,
            payment_terms_days=new.payment_terms_days,
            tax_id=new.tax_id,
        )
        for new in customers
    ]

    assert [c.account_number for c in stored] == [f"C-{n:04d}" for n in range(1, 31)]
    assert len({c.name for c in stored}) == len(stored)
    assert Counter(c.tier for c in stored) == TIERS
    assert all(c.tax_id is None or c.tax_id.startswith("00") for c in stored)  # never assigned
    gold_terms = {c.payment_terms_days for c in stored if c.tier is CustomerTier.GOLD}
    assert gold_terms <= {45, 60}


@given(seed=seeds)
def test_demo_tenants_differ_by_seed_only_in_their_customers(seed: int) -> None:
    northfield, larkspur = demo_tenants(seed)
    reference = demo_tenants(seed + 1)

    assert (northfield.products, northfield.rules) == (
        reference[0].products,
        reference[0].rules,
    )
    assert len(northfield.customers) == 80
    assert len(larkspur.customers) == 8


def _scopes_exist(tenant: DemoTenant) -> bool:
    skus = {product.sku for product in tenant.products}
    categories = {product.category for product in tenant.products}
    return all(
        (rule.sku is None or rule.sku in skus)
        and (rule.category is None or rule.category in categories)
        for rule in tenant.rules
    )


def test_demo_tenants_rules_name_their_own_products_and_categories() -> None:
    assert all(_scopes_exist(tenant) for tenant in demo_tenants(2026))


def test_demo_tenants_people_use_example_domains_and_cover_the_roles() -> None:
    northfield, larkspur = demo_tenants(2026)
    everyone = [person for tenant in (northfield, larkspur) for person in tenant.people]

    assert all(person.email.endswith(".example") for person in everyone)
    assert Counter(p.role for p in northfield.people) == {Role.SALES_REP: 3, Role.SALES_MANAGER: 1}
    assert (northfield.admin.role, larkspur.admin.role) == (Role.ADMIN, Role.ADMIN)
    assert larkspur.with_role(Role.SALES_MANAGER) == ()


def test_demo_tenants_number_their_documents_apart() -> None:
    prefixes = [(t.quote_prefix, t.order_prefix) for t in demo_tenants(2026)]

    assert prefixes == [("NF", "NFO"), ("LT", "LTO")]
