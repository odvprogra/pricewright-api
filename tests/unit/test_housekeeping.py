"""The worker's jobs on in-memory fakes (ADR-0026). Two runs at once are tested on PostgreSQL."""

import dataclasses
import hashlib
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from pricewright.application.housekeeping import (
    expire_offers,
    purge_delivered_events,
    purge_idempotency_keys,
)
from pricewright.application.ports import UnitOfWork
from pricewright.domain.audit import (
    SYSTEM_ACTOR_ID,
    ActorType,
    AuditAction,
    AuditEvent,
    AuditResourceType,
)
from pricewright.domain.events import DeliveryStatus, Event, EventType
from pricewright.domain.idempotency import KEY_LIFETIME, IdempotencyRecord, IdempotentRequest
from pricewright.domain.pricing import RateOverride
from pricewright.domain.quote_approvals import ApprovalStatus
from pricewright.domain.quote_lifecycle import EXPIRABLE, QuoteStatus
from pricewright.domain.quotes import LineChange, Quote
from tests.fakes import FakeClock, FakeUnitOfWork, InMemoryDatabase, StoredEvent
from tests.unit.quote_data import (
    BOLTS,
    MANAGER,
    NOW,
    OTHER_MANAGER,
    REP,
    TENANT,
    context,
    draft,
    margin_floor,
)

FLOOR = margin_floor("0.2")
OTHER_TENANT = uuid.uuid7()
STAGES = ("draft", "pending", "approved", "sent", "accepted", "rejected", "cancelled")


def quote_at(stage: str, *, valid_days: int, number: int, tenant: uuid.UUID = TENANT) -> Quote:
    """A quote created and moved on ``NOW``, valid for ``valid_days`` more days."""
    valid_until = NOW.date() + timedelta(days=valid_days)
    if stage in {"pending", "rejected"}:  # 30% off: it needs a manager's approval
        quote = draft(pricing=context(rules=[FLOOR]))
        clearance = LineChange(BOLTS.id, Decimal(1), RateOverride(Decimal("0.3"), "Clearance"))
        quote.add_line(clearance, by=MANAGER, context=context())
    else:
        quote = draft(LineChange(BOLTS.id, Decimal(2)), pricing=context(rules=[FLOOR]))
    quote.change_terms(valid_until=valid_until, now=NOW)
    if stage != "draft":
        quote.submit(by=REP, context=context(rules=[FLOOR]))
    if stage == "rejected":
        quote.reject(by=OTHER_MANAGER, comment="Too generous", now=NOW)
    if stage in {"sent", "accepted"}:
        quote.send(now=NOW)
    if stage == "accepted":
        quote.accept(now=NOW)
    if stage == "cancelled":
        quote.cancel(reason="Customer declined", now=NOW)
    return dataclasses.replace(quote, tenant_id=tenant, number=f"NF-2026-{number:06d}")


def database_of(quotes: Sequence[Quote]) -> InMemoryDatabase:
    return InMemoryDatabase(quotes={quote.id: quote for quote in quotes})


def expiries(database: InMemoryDatabase) -> list[AuditEvent]:
    return [e for e in database.audit_events.values() if e.action is AuditAction.QUOTE_EXPIRED]


async def run(database: InMemoryDatabase, clock: FakeClock, *, batch: int = 100) -> int:
    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(database)

    return await expire_offers(unit_of_work=unit_of_work, clock=clock, batch=batch)


async def test_expire_offers_persists_every_offer_past_its_date_in_every_tenant() -> None:
    pending = quote_at("pending", valid_days=5, number=1)
    sent = quote_at("sent", valid_days=5, number=2)
    theirs = quote_at("sent", valid_days=5, number=1, tenant=OTHER_TENANT)
    still_valid = quote_at("sent", valid_days=30, number=3)
    old_draft = quote_at("draft", valid_days=5, number=4)
    accepted = quote_at("accepted", valid_days=5, number=5)
    database = database_of([pending, sent, theirs, still_valid, old_draft, accepted])
    clock = FakeClock(NOW + timedelta(days=10))

    expired = await run(database, clock)

    assert expired == 3
    statuses = {quote.id: quote.status for quote in database.quotes.values()}
    assert statuses == {
        pending.id: QuoteStatus.EXPIRED,
        sent.id: QuoteStatus.EXPIRED,
        theirs.id: QuoteStatus.EXPIRED,
        still_valid.id: QuoteStatus.SENT,
        old_draft.id: QuoteStatus.DRAFT,
        accepted.id: QuoteStatus.ACCEPTED,
    }
    request = database.quotes[pending.id].approvals[0]
    assert (request.status, request.decided_at) == (ApprovalStatus.WITHDRAWN, clock.now)
    assert database.quotes[sent.id].status_changed_at == clock.now


async def test_expire_offers_audits_each_expiry_as_the_system() -> None:
    sent = quote_at("sent", valid_days=5, number=1)
    theirs = quote_at("pending", valid_days=5, number=1, tenant=OTHER_TENANT)
    database = database_of([sent, theirs])
    clock = FakeClock(NOW + timedelta(days=10))

    await run(database, clock)

    by_quote = {event.resource_id: event for event in expiries(database)}
    assert set(by_quote) == {sent.id, theirs.id}
    for quote, before in ((sent, "sent"), (theirs, "pending_approval")):
        event = by_quote[quote.id]
        assert (event.tenant_id, event.occurred_at) == (quote.tenant_id, clock.now)
        assert (event.actor_type, event.actor_id) == (ActorType.SYSTEM, SYSTEM_ACTOR_ID)
        assert event.changes == {"status": (before, "expired")}


async def test_expire_offers_on_the_last_valid_day_changes_nothing() -> None:
    sent = quote_at("sent", valid_days=5, number=1)
    database = database_of([sent])

    expired = await run(database, FakeClock(NOW + timedelta(days=5, hours=11)))

    assert expired == 0
    assert database.quotes[sent.id].status is QuoteStatus.SENT
    assert expiries(database) == []


async def test_expire_offers_skips_an_offer_that_changed_after_it_was_found() -> None:
    sent = quote_at("sent", valid_days=5, number=1)
    database = database_of([sent])
    clock = FakeClock(NOW + timedelta(days=10))
    opened = 0

    def unit_of_work() -> UnitOfWork:
        nonlocal opened
        opened += 1
        if opened == 2:  # after the search, before the expiry: another run expires it
            changed = dataclasses.replace(database.quotes[sent.id])
            changed.expire(now=clock.now)
            database.quotes[sent.id] = changed
        return FakeUnitOfWork(database)

    expired = await expire_offers(unit_of_work=unit_of_work, clock=clock)

    assert expired == 0
    assert database.quotes[sent.id].status is QuoteStatus.EXPIRED
    assert expiries(database) == []


async def test_expire_offers_works_through_more_offers_than_one_batch() -> None:
    offers = [quote_at("sent", valid_days=1, number=n) for n in range(1, 6)]
    database = database_of(offers)

    expired = await run(database, FakeClock(NOW + timedelta(days=3)), batch=2)

    assert expired == 5
    assert {quote.status for quote in database.quotes.values()} == {QuoteStatus.EXPIRED}


quotes = st.lists(
    st.tuples(st.sampled_from(STAGES), st.integers(0, 40), st.booleans()), max_size=12
)


@settings(max_examples=40, deadline=None)
@given(
    plan=quotes,
    days_later=st.integers(0, 60),
    runs=st.integers(1, 3),
    batch=st.integers(1, 5),
)
async def test_expire_offers_any_number_of_runs_expire_each_overdue_offer_once(
    plan: list[tuple[str, int, bool]], days_later: int, runs: int, batch: int
) -> None:
    built = [
        quote_at(stage, valid_days=days, number=n, tenant=OTHER_TENANT if theirs else TENANT)
        for n, (stage, days, theirs) in enumerate(plan, start=1)
    ]
    database = database_of(built)
    clock = FakeClock(NOW + timedelta(days=days_later))
    overdue = {
        quote.id
        for quote in built
        if quote.status in EXPIRABLE and quote.valid_until < clock.now.date()
    }

    counts = [await run(database, clock, batch=batch) for _ in range(runs)]

    assert counts == [len(overdue)] + [0] * (runs - 1)
    for quote in built:
        expected = QuoteStatus.EXPIRED if quote.id in overdue else quote.status
        assert database.quotes[quote.id].status is expected
    assert sorted(e.resource_id for e in expiries(database)) == sorted(overdue)


def key_from(tenant: uuid.UUID, created: datetime) -> IdempotencyRecord:
    """A key a rep sent with a creation at ``created``; it expires a day later."""
    return IdempotencyRecord.first(
        IdempotentRequest(str(uuid.uuid4()), hashlib.sha256(b"{}").hexdigest()),
        tenant_id=tenant,
        actor=REP,
        resource_type=AuditResourceType.QUOTE,
        resource_id=uuid.uuid7(),
        now=created,
    )


def keys_of(*records: IdempotencyRecord) -> InMemoryDatabase:
    return InMemoryDatabase(idempotency_keys={(r.tenant_id, r.actor, r.key): r for r in records})


async def purge(database: InMemoryDatabase, clock: FakeClock, *, batch: int = 1000) -> int:
    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(database)

    return await purge_idempotency_keys(unit_of_work=unit_of_work, clock=clock, batch=batch)


async def test_purge_deletes_the_expired_keys_of_every_tenant_and_keeps_live_ones() -> None:
    clock = FakeClock(NOW)
    expired_now = key_from(TENANT, NOW - KEY_LIFETIME)
    expired_before = key_from(OTHER_TENANT, NOW - KEY_LIFETIME - timedelta(hours=3))
    live = key_from(TENANT, NOW - KEY_LIFETIME + timedelta(seconds=1))
    database = keys_of(expired_now, expired_before, live)

    purged = await purge(database, clock)

    assert purged == 2
    assert list(database.idempotency_keys.values()) == [live]
    assert await purge(database, clock) == 0


async def test_purge_works_through_more_keys_than_one_batch() -> None:
    database = keys_of(*(key_from(TENANT, NOW - timedelta(days=2)) for _ in range(5)))

    purged = await purge(database, FakeClock(NOW), batch=2)

    assert purged == 5
    assert database.idempotency_keys == {}


async def test_purge_deletes_events_delivered_a_week_ago_and_keeps_the_rest() -> None:
    clock = FakeClock(NOW)

    def stored_event(
        status: DeliveryStatus = DeliveryStatus.PENDING, delivered_at: datetime | None = None
    ) -> StoredEvent:
        item = Event(uuid.uuid7(), TENANT, EventType.QUOTE_SENT, uuid.uuid7(), occurred_at=NOW)
        return StoredEvent(item, available_at=NOW, status=status, delivered_at=delivered_at)

    old = stored_event(status=DeliveryStatus.DELIVERED, delivered_at=NOW - timedelta(days=8))
    recent = stored_event(status=DeliveryStatus.DELIVERED, delivered_at=NOW - timedelta(days=6))
    dead = stored_event(status=DeliveryStatus.DEAD)
    waiting = stored_event()
    database = InMemoryDatabase(outbox_events={s.event.id: s for s in (old, recent, dead, waiting)})

    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(database)

    purged = await purge_delivered_events(unit_of_work=unit_of_work, clock=clock, batch=1)

    assert purged == 1
    assert set(database.outbox_events) == {recent.event.id, dead.event.id, waiting.event.id}
