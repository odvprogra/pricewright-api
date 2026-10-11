"""The outbox in PostgreSQL (ADR-0008): events commit with their change, in commit order."""

from datetime import UTC, datetime

import pytest
import structlog
from sqlalchemy import select

from pricewright.domain.actors import Actor
from pricewright.domain.events import EventType, quote_event
from pricewright.domain.quotes import Quote
from pricewright.infrastructure.records import OutboxEventRecord
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import Sessions
from tests.integration.stock import NOW, Stock, draft, stock

pytestmark = pytest.mark.integration


async def stored(sessions: Sessions) -> list[OutboxEventRecord]:
    async with sessions() as session:
        records = await session.scalars(select(OutboxEventRecord).order_by("position"))
        return list(records)


async def add_with_quote(sessions: Sessions, stock: Stock, quote: Quote, *, commit: bool) -> None:
    rep = Actor.person(stock.rep.id)
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(stock.tenant.id)
        await uow.quotes.add(quote)
        await uow.outbox.add(quote_event(EventType.QUOTE_SENT, quote, rep, now=NOW))
        if commit:
            await uow.commit()


async def test_outbox_stores_events_with_their_change_in_commit_order(
    session_factory: Sessions,
) -> None:
    northfield = await stock(session_factory)
    first, second = draft(northfield), draft(northfield, "NF-2026-000002")

    with structlog.contextvars.bound_contextvars(request_id="req-send"):
        await add_with_quote(session_factory, northfield, first, commit=True)
    await add_with_quote(session_factory, northfield, second, commit=True)

    one, two = await stored(session_factory)
    assert (one.aggregate_id, two.aggregate_id) == (first.id, second.id)
    assert one.position < two.position
    assert (one.tenant_id, one.type, one.aggregate_type) == (
        northfield.tenant.id,
        "quote.sent",
        "quote",
    )
    assert one.data == {
        "number": "NF-2026-000001",
        "actor_type": "user",
        "actor_id": str(northfield.rep.id),
    }
    assert (one.status, one.attempts, one.available_at) == ("pending", 0, NOW)
    assert (one.request_id, two.request_id) == ("req-send", None)
    assert (one.last_error, one.delivered_at) == (None, None)


async def test_outbox_keeps_nothing_of_a_change_that_rolled_back(session_factory: Sessions) -> None:
    northfield = await stock(session_factory)

    await add_with_quote(session_factory, northfield, draft(northfield), commit=False)

    assert await stored(session_factory) == []


async def test_outbox_takes_only_its_own_tenant_s_events(session_factory: Sessions) -> None:
    northfield, larkspur = await stock(session_factory), await stock(session_factory, "Larkspur")
    theirs = quote_event(
        EventType.QUOTE_SENT,
        draft(northfield),
        Actor.person(northfield.rep.id),
        now=datetime(2026, 10, 15, tzinfo=UTC),
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(larkspur.tenant.id)
        with pytest.raises(RuntimeError, match="unit of work's tenant"):
            await uow.outbox.add(theirs)
