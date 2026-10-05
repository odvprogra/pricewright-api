"""A quote's transitions, approvals and revisions (ADR-0005, ADR-0020), with worked examples.

The invariants over any sequence of actions are in ``test_quote_machine.py``.
"""

import copy
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from pricewright.domain.actors import Actor
from pricewright.domain.auth import PermissionDeniedError
from pricewright.domain.pricing import ApprovalReason, ArchivedProductError, RateOverride
from pricewright.domain.quote_approvals import (
    ApprovalRequest,
    ApprovalStatus,
    InvalidDecisionError,
    SelfApprovalError,
)
from pricewright.domain.quote_lifecycle import (
    InvalidTransitionError,
    QuoteAction,
    QuoteExpiredError,
    QuoteStatus,
)
from pricewright.domain.quotes import (
    EmptyQuoteError,
    InvalidQuoteError,
    LineChange,
    Quote,
    QuoteNotEditableError,
)
from tests.unit.quote_data import (
    BOLTS,
    GLOVES,
    INTEGRATION,
    MANAGER,
    NOW,
    OTHER_MANAGER,
    OTHER_REP,
    REP,
    SETTINGS,
    context,
    draft,
    margin_floor,
    product,
    usd,
    volume_tier,
)

FLOOR = margin_floor("0.2")
LATER = NOW + timedelta(hours=2)
CLEARANCE = RateOverride(Decimal("0.3"), "Clearance")


def plain_quote() -> Quote:
    """Nothing given away: submitting it needs no approval."""
    return draft(LineChange(BOLTS.id, Decimal(2)), pricing=context(rules=[FLOOR]))


def generous_quote() -> Quote:
    """30% off the bolts: above the 15% threshold and below the 20% margin floor."""
    quote = draft(pricing=context(rules=[FLOOR]))
    quote.add_line(LineChange(BOLTS.id, Decimal(1), CLEARANCE), by=MANAGER, context=context())
    return quote


def pending_quote() -> Quote:
    quote = generous_quote()
    quote.submit(by=REP, context=context(at=LATER, rules=[FLOOR]))
    return quote


def sent_quote() -> Quote:
    quote = plain_quote()
    quote.submit(by=REP, context=context(at=LATER, rules=[FLOOR]))
    quote.send(now=LATER)
    return quote


def test_submit_without_reasons_goes_straight_to_approved() -> None:
    quote = plain_quote()

    quote.submit(by=REP, context=context(at=LATER, rules=[FLOOR]))

    assert (quote.status, quote.status_changed_at) == (QuoteStatus.APPROVED, LATER)
    assert (quote.submitted_by, quote.submitted_at) == (REP, LATER)
    assert quote.approvals == []


def test_submit_prices_one_last_time_with_what_is_effective_then() -> None:
    quote = draft(LineChange(BOLTS.id, Decimal(10)))
    rules = [volume_tier(("10", "0.05"))]

    quote.submit(by=REP, context=context(at=LATER, rules=rules))

    assert quote.totals.priced_at == LATER
    assert quote.totals.net_subtotal == usd("950.00")


def test_submit_with_reasons_asks_for_approval_with_the_metric_of_the_day() -> None:
    quote = generous_quote()

    quote.submit(by=REP, context=context(at=LATER, rules=[FLOOR]))

    [request] = quote.approvals
    assert quote.status is QuoteStatus.PENDING_APPROVAL
    assert quote.pending_approval is request
    assert (request.requested_by, request.requested_at, request.status) == (
        REP,
        LATER,
        ApprovalStatus.PENDING,
    )
    assert request.reasons == (
        ApprovalReason.DISCOUNT_ABOVE_THRESHOLD,
        ApprovalReason.LINE_BELOW_MARGIN_FLOOR,
    )
    assert (request.discount, request.approval_threshold) == (Decimal("0.3000"), Decimal("0.15"))
    assert (request.list_subtotal, request.net_subtotal) == (usd("100.00"), usd("70.00"))


def test_submit_of_an_empty_quote_is_refused() -> None:
    quote = draft()

    with pytest.raises(EmptyQuoteError) as error:
        quote.submit(by=REP, context=context(at=LATER))

    assert error.value.code == "quote_empty"
    assert quote.status is QuoteStatus.DRAFT


def test_submit_needs_a_valid_until_that_has_not_passed() -> None:
    quote = draft(LineChange(BOLTS.id, Decimal(1)), valid_until=NOW.date())

    with pytest.raises(QuoteExpiredError, match="set a new date"):
        quote.submit(by=REP, context=context(at=NOW + timedelta(days=1)))

    quote.change_terms(valid_until=date(2026, 11, 30), now=NOW + timedelta(days=1))
    quote.submit(by=REP, context=context(at=NOW + timedelta(days=1)))
    assert quote.status is QuoteStatus.APPROVED


def test_submit_with_an_archived_product_changes_nothing() -> None:
    quote = plain_quote()
    archived = copy.deepcopy(BOLTS)
    archived.change(is_active=False)

    with pytest.raises(ArchivedProductError):
        quote.submit(by=REP, context=context(at=LATER, products=[archived]))

    assert (quote.status, quote.submitted_by, quote.totals.priced_at) == (
        QuoteStatus.DRAFT,
        None,
        NOW,
    )


def test_submitted_quotes_are_frozen() -> None:
    quote = pending_quote()

    with pytest.raises(QuoteNotEditableError):
        quote.add_line(LineChange(GLOVES.id, Decimal(1)), by=REP, context=context())
    with pytest.raises(InvalidTransitionError):
        quote.submit(by=REP, context=context(at=LATER))


def test_recall_withdraws_the_request_and_makes_it_a_draft_again() -> None:
    quote = pending_quote()
    [request] = quote.approvals

    quote.recall(now=LATER + timedelta(minutes=5))

    assert quote.status is QuoteStatus.DRAFT
    assert (request.status, request.decided_at) == (
        ApprovalStatus.WITHDRAWN,
        LATER + timedelta(minutes=5),
    )
    assert (quote.submitted_by, quote.submitted_at, quote.pending_approval) == (None, None, None)
    quote.change_line(quote.lines[0].id, quantity=Decimal(2), by=REP, context=context())


def test_approve_by_another_manager_records_the_decision() -> None:
    quote = pending_quote()

    quote.approve(by=OTHER_MANAGER, comment="  Strategic account.  ", now=LATER)

    [request] = quote.approvals
    assert quote.status is QuoteStatus.APPROVED
    assert (request.status, request.decided_by, request.decided_at, request.comment) == (
        ApprovalStatus.APPROVED,
        OTHER_MANAGER.id,
        LATER,
        "Strategic account.",
    )


@pytest.mark.parametrize(
    "decider",
    [REP, MANAGER],
    ids=["created-and-submitted", "set-the-override"],
)
def test_no_one_decides_on_a_quote_they_built(decider: Actor) -> None:
    quote = pending_quote()
    before = copy.deepcopy(quote)

    with pytest.raises(SelfApprovalError) as error:
        quote.approve(by=decider, now=LATER)
    with pytest.raises(SelfApprovalError):
        quote.reject(by=decider, comment="No", now=LATER)

    assert isinstance(error.value, PermissionDeniedError)  # a 403 at the API edge
    assert error.value.code == "self_approval"

    assert quote == before


def test_whoever_added_a_line_or_submitted_cannot_decide() -> None:
    quote = draft(by=REP, pricing=context(rules=[FLOOR]))
    quote.add_line(LineChange(BOLTS.id, Decimal(1), CLEARANCE), by=MANAGER, context=context())
    quote.add_line(LineChange(GLOVES.id, Decimal(1)), by=OTHER_REP, context=context())
    quote.submit(by=INTEGRATION, context=context(at=LATER, rules=[FLOOR]))

    assert quote.builders() == {REP, MANAGER, OTHER_REP, INTEGRATION}
    with pytest.raises(SelfApprovalError):
        quote.approve(by=OTHER_REP, now=LATER)


def test_integrations_never_decide_on_approvals() -> None:
    quote = pending_quote()

    with pytest.raises(PermissionDeniedError, match="only people"):
        quote.approve(by=INTEGRATION, now=LATER)


def test_reject_needs_a_comment_and_records_it() -> None:
    quote = pending_quote()

    with pytest.raises(InvalidDecisionError, match="needs a comment"):
        quote.reject(by=OTHER_MANAGER, comment="   ", now=LATER)
    with pytest.raises(InvalidDecisionError, match="at most 1000"):
        quote.reject(by=OTHER_MANAGER, comment="x" * 1001, now=LATER)
    assert quote.pending_approval is not None

    quote.reject(by=OTHER_MANAGER, comment="Keep the bolts above 20% margin.", now=LATER)

    [request] = quote.approvals
    assert quote.status is QuoteStatus.REJECTED
    assert (request.status, request.comment) == (
        ApprovalStatus.REJECTED,
        "Keep the bolts above 20% margin.",
    )


def test_a_decision_on_an_expired_offer_is_refused() -> None:
    quote = pending_quote()

    with pytest.raises(QuoteExpiredError):
        quote.approve(by=OTHER_MANAGER, now=NOW + timedelta(days=60))

    assert quote.status is QuoteStatus.PENDING_APPROVAL


def test_approved_quote_is_sent_then_accepted_within_its_validity() -> None:
    quote = sent_quote()
    assert quote.status is QuoteStatus.SENT

    quote.accept(now=LATER + timedelta(days=3))

    assert (quote.status, quote.status_changed_at) == (
        QuoteStatus.ACCEPTED,
        LATER + timedelta(days=3),
    )


def test_a_sent_quote_past_its_date_cannot_be_accepted() -> None:
    quote = sent_quote()

    with pytest.raises(QuoteExpiredError):
        quote.accept(now=NOW + timedelta(days=31))

    assert quote.status is QuoteStatus.SENT


def test_cancel_needs_a_reason_and_withdraws_a_pending_request() -> None:
    quote = pending_quote()

    with pytest.raises(InvalidQuoteError, match="reason"):
        quote.cancel(reason="  ", now=LATER)

    quote.cancel(reason=" Customer chose another supplier ", now=LATER)

    assert (quote.status, quote.cancel_reason) == (
        QuoteStatus.CANCELLED,
        "Customer chose another supplier",
    )
    assert quote.approvals[0].status is ApprovalStatus.WITHDRAWN


def test_an_accepted_quote_cannot_be_cancelled() -> None:
    quote = sent_quote()
    quote.accept(now=LATER)

    with pytest.raises(InvalidTransitionError):
        quote.cancel(reason="Changed our mind", now=LATER)


def test_revise_supersedes_the_quote_with_the_next_revision_in_draft() -> None:
    quote = sent_quote()
    quote.notes = "Delivery in two weeks."
    revised_at = NOW + timedelta(days=5)
    rules = [FLOOR, volume_tier(("2", "0.05"))]

    successor = quote.revise(by=OTHER_REP, context=context(at=revised_at, rules=rules))

    assert (quote.status, quote.superseded_by_id) == (QuoteStatus.SUPERSEDED, successor.id)
    assert (successor.number, successor.revision, successor.display_number) == (
        quote.number,
        2,
        "NF-2026-000001-R2",
    )
    assert (successor.status, successor.supersedes_id, successor.version) == (
        QuoteStatus.DRAFT,
        quote.id,
        1,
    )
    assert (successor.created_by, successor.created_at, successor.notes) == (
        OTHER_REP,
        revised_at,
        "Delivery in two weeks.",
    )
    assert successor.valid_until == date(2026, 11, 19)  # today + the tenant's 30 days
    [line] = successor.lines
    assert line.id != quote.lines[0].id
    assert (line.product_id, line.quantity, line.added_by) == (BOLTS.id, Decimal(2), REP)
    assert line.pricing.net_total == usd("190.00")  # priced again with the new volume tier


def test_revise_keeps_overrides_and_who_set_them() -> None:
    quote = pending_quote()
    quote.reject(by=OTHER_MANAGER, comment="Too generous", now=LATER)

    successor = quote.revise(by=REP, context=context(at=LATER, rules=[FLOOR]))

    [line] = successor.lines
    assert (line.override, line.override_by) == (CLEARANCE, MANAGER.id)
    assert successor.approvals == []
    assert MANAGER in successor.builders()


def test_revise_an_expired_pending_quote_withdraws_its_request() -> None:
    quote = pending_quote()
    much_later = NOW + timedelta(days=45)

    successor = quote.revise(by=REP, context=context(at=much_later, rules=[FLOOR]))

    assert quote.status is QuoteStatus.SUPERSEDED
    assert quote.approvals[0].status is ApprovalStatus.WITHDRAWN
    assert successor.valid_until == much_later.date() + timedelta(days=SETTINGS.quote_validity_days)


def test_revise_with_an_archived_product_leaves_the_quote_as_it_was() -> None:
    quote = sent_quote()
    archived = copy.deepcopy(BOLTS)
    archived.change(is_active=False)

    with pytest.raises(ArchivedProductError):
        quote.revise(by=REP, context=context(at=LATER, products=[archived]))

    assert (quote.status, quote.superseded_by_id) == (QuoteStatus.SENT, None)


def test_a_draft_is_edited_not_revised() -> None:
    with pytest.raises(InvalidTransitionError):
        plain_quote().revise(by=REP, context=context())


def test_revise_for_another_customer_is_a_programming_error() -> None:
    quote = sent_quote()
    stranger = copy.deepcopy(context().customer)
    stranger.id = uuid.uuid7()

    with pytest.raises(ValueError, match="another customer"):
        quote.revise(by=REP, context=context(at=LATER, customer=stranger))

    assert quote.status is QuoteStatus.SENT


def test_allowed_actions_follow_the_status_and_the_clock() -> None:
    quote = sent_quote()

    assert quote.allowed_actions(LATER) == {
        QuoteAction.ACCEPT,
        QuoteAction.REVISE,
        QuoteAction.CANCEL,
    }
    assert quote.allowed_actions(NOW + timedelta(days=31)) == {
        QuoteAction.REVISE,
        QuoteAction.EXPIRE,
    }


def test_a_decision_approves_or_rejects() -> None:
    request = pending_quote().approvals[0]

    with pytest.raises(ValueError, match="approves or rejects"):
        request.decide(ApprovalStatus.WITHDRAWN, by=OTHER_MANAGER, comment=None, now=LATER)


def test_withdrawn_requests_keep_their_metric() -> None:
    request = ApprovalRequest(
        id=uuid.uuid7(),
        requested_by=REP,
        requested_at=NOW,
        reasons=(ApprovalReason.DISCOUNT_ABOVE_THRESHOLD,),
        discount=Decimal("0.2"),
        approval_threshold=Decimal("0.15"),
        list_subtotal=usd("100"),
        net_subtotal=usd("80"),
    )

    request.withdraw(LATER)

    assert (request.status, request.discount, request.decided_by) == (
        ApprovalStatus.WITHDRAWN,
        Decimal("0.2"),
        None,
    )


def test_low_margin_product_needs_approval_even_at_list_price() -> None:
    thin = product("THIN-1", "100", "90")
    quote = draft(LineChange(thin.id, Decimal(1)), pricing=context(rules=[FLOOR], products=[thin]))

    quote.submit(by=REP, context=context(at=LATER, rules=[FLOOR], products=[thin]))

    assert quote.status is QuoteStatus.PENDING_APPROVAL
    assert quote.approvals[0].reasons == (ApprovalReason.LINE_BELOW_MARGIN_FLOOR,)
