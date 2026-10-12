"""The outbox relay on PostgreSQL (ADR-0008): two relays never deliver one event twice, an
aggregate's events go in order, and a crash before the commit delivers again (at least once)."""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Self

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pricewright.application.outbox import FIRST_RETRY, PermanentDeliveryError, relay_events
from pricewright.application.ports import PendingEvent, UnitOfWork
from pricewright.domain.events import Event, EventType
from pricewright.infrastructure.outbox import SqlAlchemyOutboxRelay
from pricewright.infrastructure.records import OutboxEventRecord
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import Sessions, register

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
APPROVED, SENT = EventType.QUOTE_APPROVED, EventType.QUOTE_SENT


async def written(
    sessions: Sessions, *kinds_and_aggregates: tuple[EventType, uuid.UUID]
) -> list[Event]:
    """Events of one tenant, written in this order, each in its own transaction."""
    tenant, _ = await register(sessions, "Northfield", "rep@northfield.example")
    events = [
        Event(uuid.uuid7(), tenant.id, kind, aggregate, occurred_at=NOW)
        for kind, aggregate in kinds_and_aggregates
    ]
    for item in events:
        async with SqlAlchemyUnitOfWork(sessions) as uow:
            uow.bind_tenant(tenant.id)
            await uow.outbox.add(item)
            await uow.commit()
    return events


async def rows(sessions: Sessions) -> dict[uuid.UUID, OutboxEventRecord]:
    async with sessions() as session:
        return {record.id: record for record in await session.scalars(select(OutboxEventRecord))}


class Recorder:
    def __init__(self) -> None:
        self.delivered: list[uuid.UUID] = []

    async def __call__(self, event: Event) -> None:
        self.delivered.append(event.id)


class _MeetAfterClaiming(SqlAlchemyOutboxRelay):
    """Both relays claim before either delivers or commits: their locks are held at once."""

    def __init__(self, session: AsyncSession, barrier: asyncio.Barrier) -> None:
        super().__init__(session)
        self._barrier = barrier

    async def claim(self, now: datetime, *, limit: int) -> list[PendingEvent]:
        claimed = await super().claim(now, limit=limit)
        await self._barrier.wait()
        return claimed


class _ClaimsMeet(SqlAlchemyUnitOfWork):
    def __init__(self, sessions: Sessions, barrier: asyncio.Barrier) -> None:
        super().__init__(sessions)
        self._barrier = barrier

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        self.outbox_relay = _MeetAfterClaiming(self._active_session(), self._barrier)
        return self


async def test_two_relays_at_once_never_deliver_an_event_twice(session_factory: Sessions) -> None:
    events = await written(session_factory, *((APPROVED, uuid.uuid7()) for _ in range(6)))
    both_have_claimed, consumer = asyncio.Barrier(2), Recorder()

    def unit_of_work() -> UnitOfWork:
        return _ClaimsMeet(session_factory, both_have_claimed)

    def run() -> asyncio.Task[int]:
        return asyncio.create_task(
            relay_events(
                unit_of_work=unit_of_work,
                consumers={APPROVED: consumer},
                clock=lambda: NOW,
                batch=3,
            )
        )

    claimed = await asyncio.gather(run(), run())

    assert sorted(claimed) == [3, 3]  # each took the three the other did not hold
    assert sorted(consumer.delivered) == sorted(item.id for item in events)
    assert {row.status for row in (await rows(session_factory)).values()} == {"delivered"}


async def test_relay_waits_behind_an_aggregate_s_event_another_relay_holds(
    session_factory: Sessions,
) -> None:
    quote, other = uuid.uuid7(), uuid.uuid7()
    approved, sent, unrelated = await written(
        session_factory, (APPROVED, quote), (SENT, quote), (APPROVED, other)
    )

    async with SqlAlchemyUnitOfWork(session_factory) as first:
        held = await first.outbox_relay.claim(NOW, limit=1)
        async with SqlAlchemyUnitOfWork(session_factory) as second:
            meanwhile = await second.outbox_relay.claim(NOW, limit=10)

    assert [pending.event.id for pending in held] == [approved.id]
    assert [pending.event.id for pending in meanwhile] == [unrelated.id]  # not the quote's next
    assert sent.id not in {pending.event.id for pending in meanwhile}


class _CrashBeforeCommit(SqlAlchemyUnitOfWork):
    async def commit(self) -> None:
        raise ConnectionResetError("the worker died before the commit")


async def test_a_relay_that_crashes_before_its_commit_delivers_again(
    session_factory: Sessions,
) -> None:
    [item] = await written(session_factory, (APPROVED, uuid.uuid7()))
    consumer = Recorder()

    with pytest.raises(ConnectionResetError):
        await relay_events(
            unit_of_work=lambda: _CrashBeforeCommit(session_factory),
            consumers={APPROVED: consumer},
            clock=lambda: NOW,
        )
    assert (await rows(session_factory))[item.id].status == "pending"

    await relay_events(
        unit_of_work=lambda: SqlAlchemyUnitOfWork(session_factory),
        consumers={APPROVED: consumer},
        clock=lambda: NOW,
    )

    assert consumer.delivered == [item.id, item.id]  # at least once: consumers are idempotent
    assert (await rows(session_factory))[item.id].status == "delivered"


async def test_relay_stores_retries_and_dead_letters(session_factory: Sessions) -> None:
    flaky, refused = await written(session_factory, (APPROVED, uuid.uuid7()), (SENT, uuid.uuid7()))

    async def unreachable(_: Event) -> None:
        raise OSError("connection refused")

    async def no_such_mailbox(_: Event) -> None:
        raise PermanentDeliveryError("550 no such mailbox")

    await relay_events(
        unit_of_work=lambda: SqlAlchemyUnitOfWork(session_factory),
        consumers={APPROVED: unreachable, SENT: no_such_mailbox},
        clock=lambda: NOW,
        draw=lambda: 1.0,
    )

    stored = await rows(session_factory)
    retry, dead = stored[flaky.id], stored[refused.id]
    assert (retry.status, retry.attempts, retry.available_at) == ("pending", 1, NOW + FIRST_RETRY)
    assert retry.last_error == "OSError: connection refused"
    assert (dead.status, dead.attempts, dead.delivered_at) == ("dead", 1, None)
    assert dead.last_error == "PermanentDeliveryError: 550 no such mailbox"
    assert dead.available_at == NOW  # kept as it was; an operator decides
