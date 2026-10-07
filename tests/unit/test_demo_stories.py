"""The demo tenants' stories (ADR-0024): what happens to each quote, by whom and when."""

import dataclasses
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pricewright.demo.randomness import generator
from pricewright.demo.stories import Act, Director, StoryError, tell_stories
from pricewright.demo.tenants import DemoTenant, StoryKind, demo_tenants
from pricewright.domain.customers import CustomerTier

AS_OF = date(2026, 10, 1)
CENT = Decimal("0.01")
seeds = st.integers(min_value=0, max_value=2**32)
dates = st.dates(min_value=date(2025, 1, 1), max_value=date(2031, 12, 31))
HEAVY = {
    StoryKind.WON_AFTER_REJECTION,
    StoryKind.WON_AFTER_RECALL,
    StoryKind.MANAGER_OFFER,
    StoryKind.REJECTED,
    StoryKind.EXPIRED_PENDING,
    StoryKind.PENDING,
}


def _tell(tenant: DemoTenant, seed: int = 7, as_of: date = AS_OF) -> list[object]:
    return list(tell_stories(tenant, generator(seed, "stories"), as_of))


@given(seed=seeds, as_of=dates)
def test_tell_stories_dates_each_step_after_the_last_and_before_the_as_of_date(
    seed: int, as_of: date
) -> None:
    midnight = datetime.combine(as_of, time(tzinfo=UTC))

    for tenant in demo_tenants(seed):
        stories = tell_stories(tenant, generator(seed, "stories"), as_of)

        for story in stories:
            moments = [step.at for step in story.steps]
            assert story.steps[0].act is Act.CREATE
            assert moments == sorted(moments)
            assert len(set(moments)) == len(moments)
            assert moments[-1] < midnight
            assert moments[0] >= midnight - timedelta(days=179)


@given(seed=seeds)
def test_tell_stories_tells_as_many_of_each_kind_as_the_plan_says(seed: int) -> None:
    for tenant in demo_tenants(seed):
        stories = tell_stories(tenant, generator(seed, "stories"), AS_OF)

        assert Counter(story.kind for story in stories) == tenant.stories.counts


@given(seed=seeds)
def test_tell_stories_gives_quotes_meant_for_approval_to_gold_customers(seed: int) -> None:
    northfield = demo_tenants(seed)[0]
    gold = {c.account_number for c in northfield.customers if c.tier is CustomerTier.GOLD}

    stories = tell_stories(northfield, generator(seed, "stories"), AS_OF)

    assert all(story.customer in gold for story in stories if story.kind in HEAVY)


def test_tell_stories_has_reps_build_their_own_accounts_quotes() -> None:
    northfield = demo_tenants(7)[0]
    owners, manager = northfield.account_owners, "morgan@northfield.example"

    stories = tell_stories(northfield, generator(7, "stories"), AS_OF)

    for story in stories:
        builder = manager if story.kind is StoryKind.MANAGER_OFFER else owners[story.customer]
        assert story.steps[0].by == builder


def test_tell_stories_has_the_manager_override_and_the_admin_approve_it() -> None:
    northfield = demo_tenants(7)[0]
    costs = {product.sku: product.unit_cost for product in northfield.products}

    stories = tell_stories(northfield, generator(7, "stories"), AS_OF)

    for story in (s for s in stories if s.kind is StoryKind.WON_WITH_OVERRIDE):
        by_act = {step.act: step for step in story.steps}
        just_above_cost = costs[story.lines[0][0]] * Decimal("1.05")
        assert by_act[Act.OVERRIDE].by == "morgan@northfield.example"
        assert by_act[Act.OVERRIDE].price == just_above_cost.quantize(CENT, ROUND_HALF_UP)
        assert by_act[Act.APPROVE].by == "avery@northfield.example"
        assert not by_act[Act.APPROVE].optional


def test_tell_stories_refuses_a_manager_story_without_a_manager() -> None:
    larkspur = demo_tenants(7)[1]
    plan = dataclasses.replace(larkspur.stories, counts={StoryKind.MANAGER_OFFER: 1})

    with pytest.raises(StoryError, match="no sales manager"):
        _tell(dataclasses.replace(larkspur, stories=plan))


def test_tell_stories_refuses_a_story_for_approval_without_gold_customers() -> None:
    larkspur = demo_tenants(7)[1]
    standard = tuple(dataclasses.replace(c, tier=CustomerTier.STANDARD) for c in larkspur.customers)
    plan = dataclasses.replace(larkspur.stories, counts={StoryKind.PENDING: 1})

    with pytest.raises(StoryError, match="no gold customer"):
        _tell(dataclasses.replace(larkspur, customers=standard, stories=plan))


def test_a_story_ending_on_the_as_of_date_is_refused() -> None:
    director = Director(demo_tenants(7)[1], generator(7, "stories"), AS_OF)

    script = director.script(StoryKind.DRAFT, (0, 0), heavy=False)  # created on the as-of date

    with pytest.raises(StoryError, match="would end after 2026-10-01"):
        script.story()
