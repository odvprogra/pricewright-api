"""Playing stories through the use cases (ADR-0024): a story the application contradicts stops
the load instead of leaving demo data that drifted from its script."""

import dataclasses
from datetime import UTC, date, datetime, timedelta

import pytest

from pricewright.application.ports import UnitOfWork
from pricewright.demo.clock import DemoClock
from pricewright.demo.history import HistoryError, HistoryPlayer, Roster
from pricewright.demo.seed import DEMO_PASSWORD, GO_LIVE_DAYS, GO_LIVE_HOUR, TenantLoader
from pricewright.demo.stories import Act, Step, Story
from pricewright.demo.tenants import StoryKind, demo_tenants
from pricewright.domain.quote_lifecycle import QuoteStatus
from tests.fakes import FakePasswordHasher, FakeUnitOfWork, InMemoryDatabase

AS_OF = date(2026, 10, 1)
START = datetime(2026, 9, 1, 15, tzinfo=UTC)
RILEY = "riley@larkspur.example"
HAMMER = ("LK-2001", 1)  # one hammer for a standard account: nothing to approve


async def _player() -> HistoryPlayer:
    """Larkspur's master data on the fakes, without its history."""
    database = InMemoryDatabase()

    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(database)

    larkspur = demo_tenants(7)[1]
    go_live = datetime.combine(AS_OF - timedelta(days=GO_LIVE_DAYS), GO_LIVE_HOUR)
    loader = TenantLoader(larkspur, unit_of_work, DemoClock(go_live))
    await loader.register(FakePasswordHasher(), DEMO_PASSWORD)
    await loader.load_catalog()
    await loader.load_customers()
    await loader.load_rules(AS_OF)
    roster = Roster(loader.people, loader.products, loader.customers, larkspur.currency)
    return HistoryPlayer(roster, unit_of_work, loader.clock)


def _story(*steps: Step) -> Story:
    dated = [dataclasses.replace(s, at=START + timedelta(hours=h)) for h, s in enumerate(steps)]
    return Story(StoryKind.PENDING, "L-0001", (HAMMER,), tuple(dated))


CREATE = Step(START, Act.CREATE, RILEY)
SUBMIT = Step(START, Act.SUBMIT, RILEY)


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        (
            [CREATE, Step(START, Act.SUBMIT, RILEY, expect=QuoteStatus.PENDING_APPROVAL)],
            "was approved after submitting",
        ),
        ([CREATE, SUBMIT, Step(START, Act.APPROVE, RILEY)], "had nothing to approve"),
        ([SUBMIT], "acts before its quote exists"),
        ([CREATE, Step(START, Act.CANCEL_ORDER, RILEY)], "acts on an order it has not placed"),
        ([CREATE, Step(START, Act.ADD_LINE, RILEY, sku="LK-1001")], "needs a SKU and a quantity"),
        ([CREATE, Step(START, Act.OVERRIDE, RILEY, text="why")], "needs a price and a reason"),
    ],
)
async def test_history_stops_at_a_story_the_application_contradicts(
    steps: list[Step], message: str
) -> None:
    player = await _player()

    with pytest.raises(HistoryError, match=message):
        await player.play([_story(*steps)])


async def test_history_skips_an_optional_approval_the_quote_did_not_need() -> None:
    player = await _player()
    optional = Step(START, Act.APPROVE, RILEY, optional=True)

    await player.play([_story(CREATE, SUBMIT, optional)])


def test_demo_clock_only_moves_forward() -> None:
    start = datetime(2026, 10, 1, 14, tzinfo=UTC)
    clock = DemoClock(start)

    clock.advance()
    clock.set(start + timedelta(hours=1))

    assert clock() == start + timedelta(hours=1)
    with pytest.raises(ValueError, match="cannot go back"):
        clock.set(start)
