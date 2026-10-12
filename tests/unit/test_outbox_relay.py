"""The outbox relay on in-memory fakes (ADR-0008). Two relays at once are tested on PostgreSQL."""

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from pricewright.application.outbox import (
    FIRST_RETRY,
    LONGEST_RETRY,
    MAX_ATTEMPTS,
    SHORTEST_RETRY,
    Consumer,
    PermanentDeliveryError,
    relay_events,
    retry_delay,
)
from pricewright.application.ports import UnitOfWork
from pricewright.domain.events import DeliveryStatus, Event, EventType
from tests.fakes import FakeClock, FakeUnitOfWork, InMemoryDatabase, StoredEvent

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
TENANT = uuid.uuid7()
APPROVED, SENT = EventType.QUOTE_APPROVED, EventType.QUOTE_SENT


def event(aggregate: uuid.UUID, kind: EventType = APPROVED) -> Event:
    return Event(uuid.uuid7(), TENANT, kind, aggregate, occurred_at=NOW)


def outbox(*events: Event) -> InMemoryDatabase:
    return InMemoryDatabase(outbox_events={e.id: StoredEvent(e, available_at=NOW) for e in events})


class Recorder:
    """A consumer that remembers what it got, and fails as told for some events."""

    def __init__(self, failures: dict[uuid.UUID, list[Exception]] | None = None) -> None:
        self.delivered: list[uuid.UUID] = []
        self._failures = failures or {}

    async def __call__(self, event: Event) -> None:
        planned = self._failures.get(event.id, [])
        if planned:
            raise planned.pop(0)
        self.delivered.append(event.id)


async def relay(
    database: InMemoryDatabase,
    consumers: dict[EventType, Consumer],
    clock: FakeClock,
    *,
    draw: float = 1.0,
    batch: int = 10,
) -> int:
    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(database)

    return await relay_events(
        unit_of_work=unit_of_work, consumers=consumers, clock=clock, draw=lambda: draw, batch=batch
    )


def stored(database: InMemoryDatabase, item: Event) -> StoredEvent:
    return database.outbox_events[item.id]


async def test_relay_delivers_due_events_oldest_first() -> None:
    first, second = event(uuid.uuid7()), event(uuid.uuid7(), SENT)
    database, consumer, clock = outbox(first, second), Recorder(), FakeClock(NOW)

    claimed = await relay(database, {APPROVED: consumer, SENT: consumer}, clock)

    assert claimed == 2
    assert consumer.delivered == [first.id, second.id]
    done = stored(database, first)
    assert (done.status, done.attempts, done.delivered_at) == (DeliveryStatus.DELIVERED, 1, NOW)


async def test_relay_delivers_an_event_nothing_reacts_to_without_an_attempt() -> None:
    item = event(uuid.uuid7())
    database = outbox(item)

    await relay(database, {}, FakeClock(NOW))

    done = stored(database, item)
    assert (done.status, done.attempts, done.delivered_at) == (DeliveryStatus.DELIVERED, 0, NOW)


async def test_relay_retries_a_failure_after_its_wait_and_keeps_the_error() -> None:
    item = event(uuid.uuid7())
    database, clock = outbox(item), FakeClock(NOW)
    consumer = Recorder({item.id: [ConnectionError("the mail server is down")]})

    await relay(database, {APPROVED: consumer}, clock)

    waiting = stored(database, item)
    assert (waiting.status, waiting.attempts) == (DeliveryStatus.PENDING, 1)
    assert waiting.available_at == NOW + FIRST_RETRY  # the whole ceiling with a draw of 1
    assert waiting.last_error == "ConnectionError: the mail server is down"
    clock.advance(FIRST_RETRY - timedelta(seconds=1))
    assert await relay(database, {APPROVED: consumer}, clock) == 0
    clock.advance(timedelta(seconds=1))
    await relay(database, {APPROVED: consumer}, clock)
    done = stored(database, item)
    assert (done.status, done.attempts, done.last_error) == (DeliveryStatus.DELIVERED, 2, None)
    assert consumer.delivered == [item.id]


async def test_relay_keeps_an_aggregate_s_events_in_order_behind_a_failure() -> None:
    quote, other = uuid.uuid7(), uuid.uuid7()
    approved, sent, unrelated = event(quote), event(quote, SENT), event(other)
    database, clock = outbox(approved, sent, unrelated), FakeClock(NOW)
    consumer = Recorder({approved.id: [ConnectionError("down")]})
    consumers: dict[EventType, Consumer] = {APPROVED: consumer, SENT: consumer}

    await relay(database, consumers, clock)

    assert consumer.delivered == [unrelated.id]
    assert stored(database, sent).status is DeliveryStatus.PENDING
    clock.advance(FIRST_RETRY)
    await relay(database, consumers, clock)
    assert consumer.delivered == [unrelated.id, approved.id, sent.id]


async def test_relay_gives_up_on_a_permanent_failure_at_once_and_moves_on() -> None:
    quote = uuid.uuid7()
    refused, next_one = event(quote), event(quote, SENT)
    database, clock = outbox(refused, next_one), FakeClock(NOW)
    consumer = Recorder({refused.id: [PermanentDeliveryError("550 no such mailbox")]})

    await relay(database, {APPROVED: consumer, SENT: consumer}, clock)

    dead = stored(database, refused)
    assert (dead.status, dead.attempts) == (DeliveryStatus.DEAD, 1)
    assert dead.last_error == "PermanentDeliveryError: 550 no such mailbox"
    assert consumer.delivered == [next_one.id]


async def test_relay_gives_up_after_the_last_attempt() -> None:
    item = event(uuid.uuid7())
    database, clock = outbox(item), FakeClock(NOW)
    consumer = Recorder({item.id: [TimeoutError("slow")] * MAX_ATTEMPTS})

    for _ in range(MAX_ATTEMPTS):
        await relay(database, {APPROVED: consumer}, clock)
        clock.advance(LONGEST_RETRY)

    dead = stored(database, item)
    assert (dead.status, dead.attempts, dead.last_error) == (
        DeliveryStatus.DEAD,
        MAX_ATTEMPTS,
        "TimeoutError: slow",
    )
    assert consumer.delivered == []


async def test_relay_works_through_more_events_than_one_batch() -> None:
    items = [event(uuid.uuid7()) for _ in range(25)]
    database, consumer = outbox(*items), Recorder()

    claimed = await relay(database, {APPROVED: consumer}, FakeClock(NOW), batch=10)

    assert claimed == 25
    assert consumer.delivered == [item.id for item in items]


@given(attempts=st.integers(1, 40), draw=st.floats(0, 1))
def test_retry_delay_is_a_random_share_of_an_exponential_ceiling(
    attempts: int, draw: float
) -> None:
    ceiling = min(LONGEST_RETRY, FIRST_RETRY * 2 ** min(attempts - 1, MAX_ATTEMPTS))

    delay = retry_delay(attempts, draw)

    assert SHORTEST_RETRY <= delay <= max(SHORTEST_RETRY, ceiling)


Outcome = st.sampled_from(["ok", "transient", "permanent"])


@settings(max_examples=60, deadline=None)
@given(
    plan=st.lists(
        st.tuples(st.integers(0, 2), st.lists(Outcome, max_size=MAX_ATTEMPTS + 1)), max_size=10
    ),
    draw=st.floats(0, 1),
)
async def test_relay_delivers_each_aggregate_in_order_whatever_fails(
    plan: Sequence[tuple[int, list[str]]], draw: float
) -> None:
    aggregates = [uuid.uuid7() for _ in range(3)]
    items = [event(aggregates[which]) for which, _ in plan]
    failures: dict[uuid.UUID, list[Exception]] = {}
    for item, (_, outcomes) in zip(items, plan, strict=True):
        script: list[Exception] = []
        for outcome in outcomes:
            if outcome == "ok":
                break
            script.append(PermanentDeliveryError() if outcome == "permanent" else OSError())
        failures[item.id] = script
    database, clock, consumer = outbox(*items), FakeClock(NOW), Recorder(failures)

    for _ in range(MAX_ATTEMPTS + 1):  # an hour apart: every wait has passed
        await relay(database, {APPROVED: consumer}, clock, draw=draw)
        clock.advance(LONGEST_RETRY)

    final = [stored(database, item) for item in items]
    assert {s.status for s in final} <= {DeliveryStatus.DELIVERED, DeliveryStatus.DEAD}
    assert len(consumer.delivered) == len(set(consumer.delivered))
    position = {item.id: index for index, item in enumerate(items)}
    by_aggregate: dict[uuid.UUID, list[int]] = defaultdict(list)
    for delivered in consumer.delivered:
        by_aggregate[database.outbox_events[delivered].event.aggregate_id].append(
            position[delivered]
        )
    assert all(order == sorted(order) for order in by_aggregate.values())
    for item, s in zip(items, final, strict=True):
        assert (s.status is DeliveryStatus.DELIVERED) == (item.id in consumer.delivered)
