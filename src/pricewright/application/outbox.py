"""The outbox relay (ADR-0008): each event to its consumer, at least once, in order per aggregate.

The relay claims due events in one unit of work, hands each to the consumer of its type and records
what happened in the same unit of work: a crash before the commit releases the events, which are
delivered again. A transient failure is retried later with exponential backoff and full jitter; a
permanent one, or the last attempt, leaves the event dead for an operator.
"""

import random
from collections.abc import Awaitable, Callable, Collection, Mapping
from datetime import datetime, timedelta
from uuid import UUID

from pricewright.application.ports import (
    Clock,
    DeadLetter,
    DeliveryOutcome,
    PendingEvent,
    UnitOfWorkFactory,
)
from pricewright.domain.events import MAX_ERROR_LENGTH, DeliveryStatus, Event, EventType

MAX_ATTEMPTS = 12
FIRST_RETRY = timedelta(seconds=5)
LONGEST_RETRY = timedelta(hours=1)
SHORTEST_RETRY = timedelta(seconds=1)
"""Never sooner than the relay's next poll, so a run never retries what it just failed."""
RELAY_BATCH = 10
"""Events claimed per transaction; the transaction stays open while their consumers run."""

type Consumer = Callable[[Event], Awaitable[None]]
"""Reacts to an event; must be idempotent, since an event may be delivered more than once."""


class PermanentDeliveryError(Exception):
    """No retry can succeed, such as an address the mail server refuses: give up at once."""


def retry_delay(attempts: int, draw: float) -> timedelta:
    """How long to wait after the ``attempts``-th failure: a random share (``draw``, 0 to 1) of
    an exponential ceiling, ``FIRST_RETRY * 2 ** (attempts - 1)`` up to ``LONGEST_RETRY`` (full
    jitter), and at least ``SHORTEST_RETRY``."""
    doublings: int = 2 ** min(attempts - 1, MAX_ATTEMPTS)
    ceiling = min(LONGEST_RETRY, FIRST_RETRY * doublings)
    return max(SHORTEST_RETRY, ceiling * draw)


def _error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:MAX_ERROR_LENGTH]


async def _deliver(
    pending: PendingEvent, consumer: Consumer | None, *, now: datetime, draw: float
) -> DeliveryOutcome:
    if consumer is None:  # nothing reacts to this type (yet): nothing to deliver
        return DeliveryOutcome(DeliveryStatus.DELIVERED, pending.attempts, now)
    attempts = pending.attempts + 1
    try:
        await consumer(pending.event)
    except PermanentDeliveryError as error:
        return DeliveryOutcome(DeliveryStatus.DEAD, attempts, now, _error(error))
    except Exception as error:  # any other failure may pass: retry it later
        if attempts >= MAX_ATTEMPTS:
            return DeliveryOutcome(DeliveryStatus.DEAD, attempts, now, _error(error))
        later = now + retry_delay(attempts, draw)
        return DeliveryOutcome(DeliveryStatus.PENDING, attempts, later, _error(error))
    return DeliveryOutcome(DeliveryStatus.DELIVERED, attempts, now)


async def relay_events(
    *,
    unit_of_work: UnitOfWorkFactory,
    consumers: Mapping[EventType, Consumer],
    clock: Clock,
    draw: Callable[[], float] = random.random,
    batch: int = RELAY_BATCH,
) -> int:
    """Deliver the due events, a batch per transaction, until none is left. Return how many
    events were claimed.

    A batch holds one event per aggregate, its earliest; the next comes with the next batch. The
    loop ends: a failed event waits at least ``SHORTEST_RETRY`` before it is due again.
    """
    claimed = 0
    while True:
        now = clock()
        async with unit_of_work() as uow:
            due = await uow.outbox_relay.claim(now, limit=batch)
            for pending in due:
                consumer = consumers.get(pending.event.type)
                outcome = await _deliver(pending, consumer, now=now, draw=draw())
                await uow.outbox_relay.record(pending.event.id, outcome)
            await uow.commit()
        if not due:
            return claimed
        claimed += len(due)


async def list_dead_letters(*, unit_of_work: UnitOfWorkFactory, limit: int) -> list[DeadLetter]:
    """The events the relay gave up on, oldest first, for an operator to look into."""
    async with unit_of_work() as uow:
        return await uow.outbox_relay.dead_letters(limit=limit)


async def revive_dead_letters(
    event_ids: Collection[UUID] | None, *, unit_of_work: UnitOfWorkFactory, clock: Clock
) -> int:
    """Give dead events (every one, for ``None``) a new round of attempts, once an operator has
    fixed what made them fail. Return how many."""
    async with unit_of_work() as uow:
        revived = await uow.outbox_relay.revive(event_ids, now=clock())
        await uow.commit()
    return revived
