"""Which quote changes leave an event in the outbox for the worker (ADR-0008), on the fakes."""

from datetime import timedelta

import pytest
import structlog

from pricewright.application.quotes import accept_quote, reject_quote
from pricewright.domain.audit import AuditResourceType
from pricewright.domain.events import Event, EventType
from pricewright.domain.quote_lifecycle import QuoteExpiredError
from pricewright.domain.quotes import Quote
from tests.unit.test_quote_approval_management import approve, manager, pending
from tests.unit.test_quote_management import Fixture
from tests.unit.test_quote_transition_management import generous, send, submit


@pytest.fixture
def f() -> Fixture:
    return Fixture()


def events(f: Fixture) -> list[Event]:
    return [stored.event for stored in f.database.outbox_events.values()]


async def test_approving_a_quote_leaves_an_event_for_whoever_submitted_it(f: Fixture) -> None:
    quote = await pending(f)
    decider = manager(f)

    with structlog.contextvars.bound_contextvars(request_id="req-approve"):
        approved = await approve(f, quote, decider)

    [event] = events(f)
    assert (event.type, event.tenant_id) == (EventType.QUOTE_APPROVED, f.northfield.id)
    assert (event.aggregate_type, event.aggregate_id) == (AuditResourceType.QUOTE, quote.id)
    assert event.id.version == 7
    assert event.occurred_at == f.clock.now
    assert event.data == {
        "number": approved.display_number,
        "actor_type": "user",
        "actor_id": str(decider.subject_id),
    }
    assert event.request_id == "req-approve"


async def test_rejecting_and_sending_leave_their_events(f: Fixture) -> None:
    rejected = await pending(f)
    await reject_quote(
        manager(f),
        rejected.id,
        "Keep the bolts above their floor.",
        expected_version=f.database.quotes[rejected.id].version,
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )
    sent = await send(f, await submit(f, await f.create()))

    assert [(e.type, e.aggregate_id) for e in events(f)] == [
        (EventType.QUOTE_REJECTED, rejected.id),
        (EventType.QUOTE_SENT, sent.id),
    ]


async def test_changes_nobody_reacts_to_leave_no_event(f: Fixture) -> None:
    quote = await send(f, await submit(f, await f.create()))
    f.database.outbox_events.clear()

    await accept_quote(
        f.rep,
        quote.id,
        expected_version=f.database.quotes[quote.id].version,
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )
    await submit(f, await generous(f))

    assert events(f) == []


async def test_a_refused_change_leaves_no_event(f: Fixture) -> None:
    quote: Quote = await pending(f)
    f.clock.advance(timedelta(days=60))  # past its date: it can only be revised

    with pytest.raises(QuoteExpiredError):
        await approve(f, quote, manager(f))

    assert events(f) == []
