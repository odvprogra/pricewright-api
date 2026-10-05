"""Moving quotes through their lifecycle with the use cases, with in-memory fakes."""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from pricewright.application.quotes import (
    NewQuote,
    accept_quote,
    add_quote_line,
    cancel_quote,
    recall_quote,
    revise_quote,
    send_quote,
    submit_quote,
)
from pricewright.domain.audit import AuditAction, AuditEvent
from pricewright.domain.auth import GRANTABLE_SCOPES, Permission, PermissionDeniedError, Principal
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.pricing import RateOverride
from pricewright.domain.quote_approvals import ApprovalStatus
from pricewright.domain.quote_lifecycle import QuoteExpiredError, QuoteStatus
from pricewright.domain.quotes import EmptyQuoteError, LineChange, Quote
from pricewright.domain.users import Role
from tests.unit.test_quote_management import Fixture


@pytest.fixture
def f() -> Fixture:
    return Fixture()


def events(f: Fixture, action: AuditAction) -> list[AuditEvent]:
    return [event for event in f.database.audit_events.values() if event.action is action]


def version(f: Fixture, quote: Quote) -> int:
    return f.database.quotes[quote.id].version


async def generous(f: Fixture) -> Quote:
    """30% off by a manager: above the 15% threshold, so submitting asks for approval."""
    quote = await f.create()
    manager = Principal(f.northfield.id, uuid.uuid7(), Role.SALES_MANAGER)
    override = RateOverride(Decimal("0.3"), "Clearance")
    return await add_quote_line(
        manager,
        quote.id,
        LineChange(f.bolts.id, Decimal(10), override),
        expected_version=1,
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )


async def submit(f: Fixture, quote: Quote, caller: Principal | None = None) -> Quote:
    return await submit_quote(
        caller or f.rep,
        quote.id,
        expected_version=version(f, quote),
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )


async def send(f: Fixture, quote: Quote, caller: Principal | None = None) -> Quote:
    return await send_quote(
        caller or f.rep,
        quote.id,
        expected_version=version(f, quote),
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )


async def test_submit_quote_without_reasons_approves_it_and_records_it(f: Fixture) -> None:
    quote = await f.create()

    submitted = await submit(f, quote)

    assert (submitted.status, submitted.version) == (QuoteStatus.APPROVED, 2)
    assert f.database.quotes[quote.id].status is QuoteStatus.APPROVED
    [event] = events(f, AuditAction.QUOTE_SUBMITTED)
    assert event.changes == {"status": ("draft", "approved")}


async def test_submit_quote_with_reasons_opens_an_approval_request(f: Fixture) -> None:
    quote = await generous(f)

    submitted = await submit(f, quote)

    assert submitted.status is QuoteStatus.PENDING_APPROVAL
    assert submitted.approvals[0].status is ApprovalStatus.PENDING
    [event] = events(f, AuditAction.QUOTE_SUBMITTED)
    assert event.changes == {
        "status": ("draft", "pending_approval"),
        "approval_reasons": (None, "discount_above_threshold"),
    }


async def test_an_integration_submits_drafts(f: Fixture) -> None:
    integration = f.integration(Permission.QUOTES_MANAGE)
    quote = await f.create(caller=integration)

    submitted = await submit(f, quote, integration)

    assert submitted.submitted_by is not None
    assert not submitted.submitted_by.is_person


async def test_submit_quote_refuses_an_empty_draft_and_an_old_version(f: Fixture) -> None:
    empty = await f.create(NewQuote(f.acme.id))
    with pytest.raises(EmptyQuoteError):
        await submit(f, empty)

    quote = await f.create()
    with pytest.raises(StaleVersionError):
        await submit_quote(
            f.rep, quote.id, expected_version=7, unit_of_work=f.unit_of_work, clock=f.clock
        )


async def test_recall_quote_makes_it_a_draft_again(f: Fixture) -> None:
    quote = await submit(f, await generous(f))

    recalled = await recall_quote(
        f.rep,
        quote.id,
        expected_version=version(f, quote),
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )

    assert recalled.status is QuoteStatus.DRAFT
    assert recalled.approvals[0].status is ApprovalStatus.WITHDRAWN
    [event] = events(f, AuditAction.QUOTE_RECALLED)
    assert event.changes == {"status": ("pending_approval", "draft")}


async def test_send_and_accept_record_each_commitment(f: Fixture) -> None:
    quote = await send(f, await submit(f, await f.create()))

    accepted = await accept_quote(
        f.rep,
        quote.id,
        expected_version=version(f, quote),
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )

    assert accepted.status is QuoteStatus.ACCEPTED
    assert [e.changes for e in events(f, AuditAction.QUOTE_SENT)] == [
        {"status": ("approved", "sent")}
    ]
    assert [e.changes for e in events(f, AuditAction.QUOTE_ACCEPTED)] == [
        {"status": ("sent", "accepted")}
    ]


async def test_integrations_never_send_or_accept(f: Fixture) -> None:
    quote = await submit(f, await f.create())
    integration = Principal(f.northfield.id, uuid.uuid7(), scopes=GRANTABLE_SCOPES)

    with pytest.raises(PermissionDeniedError, match="quotes:send"):
        await send(f, quote, integration)
    with pytest.raises(PermissionDeniedError, match="quotes:send"):
        await accept_quote(
            integration, quote.id, expected_version=2, unit_of_work=f.unit_of_work, clock=f.clock
        )


async def test_an_expired_quote_cannot_be_accepted(f: Fixture) -> None:
    quote = await send(f, await submit(f, await f.create()))
    f.clock.advance(timedelta(days=45))

    with pytest.raises(QuoteExpiredError):
        await accept_quote(
            f.rep,
            quote.id,
            expected_version=version(f, quote),
            unit_of_work=f.unit_of_work,
            clock=f.clock,
        )

    assert f.database.quotes[quote.id].status is QuoteStatus.SENT


async def test_cancel_quote_keeps_its_reason(f: Fixture) -> None:
    quote = await send(f, await submit(f, await f.create()))

    cancelled = await cancel_quote(
        f.rep,
        quote.id,
        "Customer chose another bid",
        expected_version=version(f, quote),
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )

    assert (cancelled.status, cancelled.cancel_reason) == (
        QuoteStatus.CANCELLED,
        "Customer chose another bid",
    )
    [event] = events(f, AuditAction.QUOTE_CANCELLED)
    assert event.changes == {
        "status": ("sent", "cancelled"),
        "cancel_reason": (None, "Customer chose another bid"),
    }


async def test_revise_quote_stores_both_revisions_and_records_both(f: Fixture) -> None:
    quote = await send(f, await submit(f, await f.create()))

    successor = await revise_quote(
        f.rep,
        quote.id,
        expected_version=version(f, quote),
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )

    old = f.database.quotes[quote.id]
    assert (old.status, old.superseded_by_id) == (QuoteStatus.SUPERSEDED, successor.id)
    assert f.database.quotes[successor.id] == successor
    assert (successor.display_number, successor.status) == ("NF-2026-000001-R2", QuoteStatus.DRAFT)
    [revised] = events(f, AuditAction.QUOTE_REVISED)
    assert revised.changes == {
        "status": ("sent", "superseded"),
        "superseded_by_id": (None, str(successor.id)),
    }
    created = [e for e in events(f, AuditAction.QUOTE_CREATED) if e.resource_id == successor.id]
    assert created[0].changes["supersedes_id"] == (None, str(quote.id))
    assert created[0].changes["number"] == (None, "NF-2026-000001-R2")


async def test_revise_quote_based_on_an_old_version_adds_nothing(f: Fixture) -> None:
    quote = await send(f, await submit(f, await f.create()))

    with pytest.raises(StaleVersionError):
        await revise_quote(
            f.rep, quote.id, expected_version=1, unit_of_work=f.unit_of_work, clock=f.clock
        )

    assert list(f.database.quotes) == [quote.id]


async def test_transitions_never_reach_another_tenants_quote(f: Fixture) -> None:
    quote = await f.create()
    stranger = Principal(f.larkspur.id, uuid.uuid7(), Role.ADMIN)

    with pytest.raises(NotFoundError):
        await submit(f, quote, stranger)
