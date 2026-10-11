"""The worker's jobs on PostgreSQL (ADR-0026): the search across tenants, the system's audit
events, and two runs at once."""

import asyncio
from datetime import date, timedelta
from typing import Self
from uuid import UUID

import pytest
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from pricewright.application.housekeeping import expire_offers
from pricewright.application.ports import AuditEventFilter, TenantRecordRef, UnitOfWork
from pricewright.domain.actors import Actor
from pricewright.domain.audit import SYSTEM_ACTOR_ID, ActorType, AuditAction, AuditEvent
from pricewright.domain.quote_approvals import ApprovalStatus
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.quotes import Quote
from pricewright.infrastructure.quotes import SqlAlchemyQuoteRepository
from pricewright.infrastructure.repositories import TenantScope
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import Sessions
from tests.integration.stock import NOW, Stock, accepted, draft, stock

pytestmark = pytest.mark.integration

# The stock's drafts are valid until 2026-11-14.
LAST_VALID_DAY = date(2026, 11, 14)
LATER = NOW + timedelta(days=45)


async def store(sessions: Sessions, stock: Stock, quote: Quote) -> Quote:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(stock.tenant.id)
        await uow.quotes.add(quote)
        await uow.commit()
    return quote


def pending(stock: Stock, number: str) -> Quote:
    """Submitted, waiting for a decision: a line is below its margin floor."""
    quote = draft(stock, number)
    quote.submit(by=Actor.person(stock.rep.id), context=stock.pricing())
    return quote


def sent(stock: Stock, number: str) -> Quote:
    quote = pending(stock, number)
    quote.approve(by=Actor.person(stock.director.id), now=NOW)
    quote.send(now=NOW)
    return quote


async def expiry_events(sessions: Sessions, stock: Stock, quote_id: UUID) -> list[AuditEvent]:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(stock.tenant.id)
        where = AuditEventFilter(resource_id=quote_id, action=AuditAction.QUOTE_EXPIRED)
        return await uow.audit_events.page(where, before=None, limit=10)


async def test_housekeeping_finds_offers_past_their_date_in_every_tenant(
    session_factory: Sessions,
) -> None:
    northfield, larkspur = await stock(session_factory), await stock(session_factory, "Larkspur")
    ours = await store(session_factory, northfield, sent(northfield, "NF-2026-000001"))
    theirs = await store(session_factory, larkspur, pending(larkspur, "LT-2026-000001"))
    await store(session_factory, northfield, draft(northfield, "NF-2026-000002"))
    await accepted(session_factory, northfield, "NF-2026-000003")
    due = sorted(
        [
            TenantRecordRef(northfield.tenant.id, ours.id),
            TenantRecordRef(larkspur.tenant.id, theirs.id),
        ],
        key=lambda ref: ref.id,
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        on_the_last_day = await uow.housekeeping.offers_past_validity(
            LAST_VALID_DAY, after=None, limit=10
        )
        day_after = LAST_VALID_DAY + timedelta(days=1)
        found = await uow.housekeeping.offers_past_validity(day_after, after=None, limit=10)
        first = await uow.housekeeping.offers_past_validity(day_after, after=None, limit=1)
        rest = await uow.housekeeping.offers_past_validity(day_after, after=first[0].id, limit=10)

    assert on_the_last_day == []
    assert found == due
    assert first + rest == due


async def test_expire_offers_stores_the_expiry_and_the_system_s_event(
    session_factory: Sessions,
) -> None:
    northfield = await stock(session_factory)
    quote = await store(session_factory, northfield, pending(northfield, "NF-2026-000001"))

    def unit_of_work() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    with structlog.contextvars.bound_contextvars(request_id="job-run-1"):
        expired = await expire_offers(unit_of_work=unit_of_work, clock=lambda: LATER)

    assert expired == 1
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.tenant.id)
        stored = await uow.quotes.get(quote.id)
    assert stored is not None
    assert (stored.status, stored.status_changed_at) == (QuoteStatus.EXPIRED, LATER)
    assert stored.approvals[0].status is ApprovalStatus.WITHDRAWN
    [event] = await expiry_events(session_factory, northfield, quote.id)
    assert (event.actor_type, event.actor_id) == (ActorType.SYSTEM, SYSTEM_ACTOR_ID)
    assert event.changes == {"status": ("pending_approval", "expired")}
    assert event.request_id == "job-run-1"


class _MeetAfterReading(SqlAlchemyQuoteRepository):
    """Each run reads the quote, then waits for the other: both expire the same version."""

    def __init__(self, session: AsyncSession, scope: TenantScope, barrier: asyncio.Barrier) -> None:
        super().__init__(session, scope)
        self._barrier = barrier

    async def get(self, quote_id: UUID) -> Quote | None:
        quote = await super().get(quote_id)
        await self._barrier.wait()
        return quote


class _ReadersMeet(SqlAlchemyUnitOfWork):
    def __init__(self, sessions: Sessions, barrier: asyncio.Barrier) -> None:
        super().__init__(sessions)
        self._barrier = barrier

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        self.quotes = _MeetAfterReading(self._active_session(), self._scope, self._barrier)
        return self


async def test_two_expiry_runs_at_once_expire_an_offer_once(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)
    quote = await store(session_factory, northfield, sent(northfield, "NF-2026-000001"))
    both_have_read = asyncio.Barrier(2)

    def unit_of_work() -> UnitOfWork:
        return _ReadersMeet(session_factory, both_have_read)

    results = await asyncio.gather(
        expire_offers(unit_of_work=unit_of_work, clock=lambda: LATER),
        expire_offers(unit_of_work=unit_of_work, clock=lambda: LATER),
    )

    assert sorted(results) == [0, 1]
    assert len(await expiry_events(session_factory, northfield, quote.id)) == 1
