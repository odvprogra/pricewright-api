"""Approving and rejecting quotes, and the approval inbox, through the use cases (ADR-0020)."""

import uuid
from datetime import timedelta

import pytest

from pricewright.application.quotes import (
    approve_quote,
    list_approval_requests,
    reject_quote,
)
from pricewright.domain.audit import AuditAction, AuditEvent
from pricewright.domain.auth import GRANTABLE_SCOPES, PermissionDeniedError, Principal
from pricewright.domain.quote_approvals import (
    ApprovalStatus,
    InvalidDecisionError,
    SelfApprovalError,
)
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.quotes import Quote
from pricewright.domain.users import Role
from tests.unit.test_quote_management import Fixture
from tests.unit.test_quote_transition_management import generous, submit


@pytest.fixture
def f() -> Fixture:
    return Fixture()


def manager(f: Fixture) -> Principal:
    return Principal(f.northfield.id, uuid.uuid7(), Role.SALES_MANAGER)


def events(f: Fixture, action: AuditAction) -> list[AuditEvent]:
    return [event for event in f.database.audit_events.values() if event.action is action]


async def pending(f: Fixture) -> Quote:
    return await submit(f, await generous(f))


async def approve(f: Fixture, quote: Quote, caller: Principal, comment: str | None = None) -> Quote:
    return await approve_quote(
        caller,
        quote.id,
        comment,
        expected_version=f.database.quotes[quote.id].version,
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )


async def inbox(
    f: Fixture, status: ApprovalStatus = ApprovalStatus.PENDING, caller: Principal | None = None
) -> list[uuid.UUID]:
    page = await list_approval_requests(
        caller or f.rep,
        status,
        after=None,
        limit=10,
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )
    return [item.quote.id for item in page.items]


async def test_another_manager_approves_and_it_is_recorded(f: Fixture) -> None:
    quote = await pending(f)
    boss = manager(f)

    approved = await approve(f, quote, boss, "Strategic account")

    assert approved.status is QuoteStatus.APPROVED
    assert approved.approvals[0].decided_by == boss.subject_id
    [event] = events(f, AuditAction.QUOTE_APPROVED)
    assert event.changes == {
        "status": ("pending_approval", "approved"),
        "approval_comment": (None, "Strategic account"),
    }


async def test_an_approval_without_a_comment_records_only_the_status(f: Fixture) -> None:
    quote = await pending(f)

    await approve(f, quote, manager(f))

    [event] = events(f, AuditAction.QUOTE_APPROVED)
    assert event.changes == {"status": ("pending_approval", "approved")}


async def test_the_manager_who_set_the_override_cannot_approve(f: Fixture) -> None:
    quote = await pending(f)
    overrider = quote.lines[-1].override_by
    assert overrider is not None

    with pytest.raises(SelfApprovalError):
        await approve(f, quote, Principal(f.northfield.id, overrider, Role.SALES_MANAGER))

    assert f.database.quotes[quote.id].status is QuoteStatus.PENDING_APPROVAL


async def test_reps_and_integrations_never_approve(f: Fixture) -> None:
    quote = await pending(f)
    integration = Principal(f.northfield.id, uuid.uuid7(), scopes=GRANTABLE_SCOPES)

    for caller in (Principal(f.northfield.id, uuid.uuid7(), Role.SALES_REP), integration):
        with pytest.raises(PermissionDeniedError, match="quotes:approve"):
            await approve(f, quote, caller)


async def test_reject_needs_a_comment_and_records_it(f: Fixture) -> None:
    quote = await pending(f)
    boss = manager(f)

    with pytest.raises(InvalidDecisionError):
        await reject_quote(
            boss, quote.id, "  ", expected_version=3, unit_of_work=f.unit_of_work, clock=f.clock
        )
    rejected = await reject_quote(
        boss,
        quote.id,
        "Keep the bolts above 20%",
        expected_version=3,
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )

    assert rejected.status is QuoteStatus.REJECTED
    [event] = events(f, AuditAction.QUOTE_REJECTED)
    assert event.changes == {
        "status": ("pending_approval", "rejected"),
        "approval_comment": (None, "Keep the bolts above 20%"),
    }


async def test_the_inbox_lists_pending_requests_oldest_first(f: Fixture) -> None:
    first, second = await pending(f), await pending(f)
    await f.create()  # a draft has no request

    assert await inbox(f) == [first.id, second.id]


async def test_decided_requests_leave_the_inbox_and_can_be_listed(f: Fixture) -> None:
    quote = await pending(f)
    await approve(f, quote, manager(f))

    assert await inbox(f) == []
    assert await inbox(f, ApprovalStatus.APPROVED) == [quote.id]


async def test_expired_offers_leave_the_inbox(f: Fixture) -> None:
    await pending(f)
    f.clock.advance(timedelta(days=45))

    assert await inbox(f) == []


async def test_the_inbox_pages_by_request(f: Fixture) -> None:
    first, second = await pending(f), await pending(f)

    page = await list_approval_requests(
        f.rep,
        ApprovalStatus.PENDING,
        after=None,
        limit=1,
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )
    rest = await list_approval_requests(
        f.rep,
        ApprovalStatus.PENDING,
        after=page.next_after,
        limit=1,
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )

    assert [item.quote.id for item in page.items] == [first.id]
    assert [item.quote.id for item in rest.items] == [second.id]
    assert rest.next_after is None


async def test_the_inbox_never_shows_another_tenant(f: Fixture) -> None:
    await pending(f)
    stranger = Principal(f.larkspur.id, uuid.uuid7(), Role.ADMIN)

    assert await inbox(f, caller=stranger) == []
