"""The quote lifecycle's transition table and its expiry guard (ADR-0005)."""

from collections import deque
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pricewright.domain.errors import ConflictError
from pricewright.domain.quote_lifecycle import (
    EXPIRABLE,
    TERMINAL,
    TRANSITIONS,
    InvalidTransitionError,
    QuoteAction,
    QuoteExpiredError,
    QuoteStatus,
    allowed_actions,
    effective_status,
    has_passed,
    targets,
)

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
VALID = date(2026, 11, 14)
PAST = date(2026, 10, 14)


def test_quote_lifecycle_terminal_statuses_are_cancelled_converted_and_superseded() -> None:
    assert {QuoteStatus.CANCELLED, QuoteStatus.CONVERTED, QuoteStatus.SUPERSEDED} == TERMINAL


def test_quote_lifecycle_submit_leads_to_pending_approval_or_straight_to_approved() -> None:
    reached = targets(QuoteStatus.DRAFT, QuoteAction.SUBMIT, valid_until=VALID, now=NOW)

    assert reached == {QuoteStatus.PENDING_APPROVAL, QuoteStatus.APPROVED}


@pytest.mark.parametrize(
    ("status", "action", "target"),
    [
        (QuoteStatus.PENDING_APPROVAL, QuoteAction.APPROVE, QuoteStatus.APPROVED),
        (QuoteStatus.PENDING_APPROVAL, QuoteAction.REJECT, QuoteStatus.REJECTED),
        (QuoteStatus.PENDING_APPROVAL, QuoteAction.RECALL, QuoteStatus.DRAFT),
        (QuoteStatus.APPROVED, QuoteAction.SEND, QuoteStatus.SENT),
        (QuoteStatus.APPROVED, QuoteAction.REVISE, QuoteStatus.SUPERSEDED),
        (QuoteStatus.SENT, QuoteAction.ACCEPT, QuoteStatus.ACCEPTED),
        (QuoteStatus.SENT, QuoteAction.REVISE, QuoteStatus.SUPERSEDED),
        (QuoteStatus.REJECTED, QuoteAction.REVISE, QuoteStatus.SUPERSEDED),
        (QuoteStatus.ACCEPTED, QuoteAction.CONVERT, QuoteStatus.CONVERTED),
    ],
)
def test_quote_lifecycle_follows_the_brief_and_the_agreed_additions(
    status: QuoteStatus, action: QuoteAction, target: QuoteStatus
) -> None:
    assert targets(status, action, valid_until=VALID, now=NOW) == {target}


@pytest.mark.parametrize(
    "status",
    [
        QuoteStatus.DRAFT,
        QuoteStatus.PENDING_APPROVAL,
        QuoteStatus.APPROVED,
        QuoteStatus.SENT,
        QuoteStatus.REJECTED,
    ],
)
def test_quote_lifecycle_cancels_every_open_quote(status: QuoteStatus) -> None:
    reached = targets(status, QuoteAction.CANCEL, valid_until=VALID, now=NOW)

    assert reached == {QuoteStatus.CANCELLED}


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (QuoteStatus.DRAFT, QuoteAction.APPROVE),
        (QuoteStatus.DRAFT, QuoteAction.REVISE),
        (QuoteStatus.APPROVED, QuoteAction.ACCEPT),
        (QuoteStatus.ACCEPTED, QuoteAction.CANCEL),
        (QuoteStatus.ACCEPTED, QuoteAction.REVISE),
        (QuoteStatus.SUPERSEDED, QuoteAction.ACCEPT),
    ],
)
def test_quote_lifecycle_refuses_an_action_its_status_does_not_allow(
    status: QuoteStatus, action: QuoteAction
) -> None:
    label = status.replace("_", " ")
    with pytest.raises(
        InvalidTransitionError, match=f"cannot {action} a quote that is {label}"
    ) as error:
        targets(status, action, valid_until=VALID, now=NOW)

    assert isinstance(error.value, ConflictError)  # a 409 at the API edge
    assert error.value.code == "invalid_transition"


def test_quote_lifecycle_sent_quote_past_its_date_cannot_be_accepted() -> None:
    with pytest.raises(QuoteExpiredError, match="expired on 2026-10-14") as error:
        targets(QuoteStatus.SENT, QuoteAction.ACCEPT, valid_until=PAST, now=NOW)

    assert isinstance(error.value, ConflictError)
    assert error.value.code == "quote_expired"


@pytest.mark.parametrize("status", sorted(EXPIRABLE))
def test_quote_lifecycle_expired_offer_can_still_be_revised(status: QuoteStatus) -> None:
    reached = targets(status, QuoteAction.REVISE, valid_until=PAST, now=NOW)

    assert reached == {QuoteStatus.SUPERSEDED}


def test_quote_lifecycle_expired_pending_approval_cannot_be_decided_or_recalled() -> None:
    for action in (QuoteAction.APPROVE, QuoteAction.REJECT, QuoteAction.RECALL):
        with pytest.raises(QuoteExpiredError):
            targets(QuoteStatus.PENDING_APPROVAL, action, valid_until=PAST, now=NOW)


def test_quote_lifecycle_quote_is_valid_through_its_last_day_in_utc() -> None:
    last_moment = datetime(2026, 11, 14, 23, 59, 59, tzinfo=UTC)
    next_day = datetime(2026, 11, 15, tzinfo=UTC)
    la_paz = timezone(timedelta(hours=-4))

    assert not has_passed(VALID, last_moment)
    assert has_passed(VALID, next_day)
    assert has_passed(VALID, datetime(2026, 11, 14, 21, tzinfo=la_paz))  # 01:00 UTC on the 15th


@pytest.mark.parametrize("status", sorted(EXPIRABLE))
def test_quote_lifecycle_expire_persists_what_the_clock_decided(status: QuoteStatus) -> None:
    reached = targets(status, QuoteAction.EXPIRE, valid_until=PAST, now=NOW)

    assert reached == {QuoteStatus.EXPIRED}


def test_quote_lifecycle_expire_before_the_date_is_refused() -> None:
    with pytest.raises(InvalidTransitionError, match="valid until 2026-11-14"):
        targets(QuoteStatus.SENT, QuoteAction.EXPIRE, valid_until=VALID, now=NOW)


def test_quote_lifecycle_persisted_expiry_cannot_expire_again() -> None:
    assert allowed_actions(QuoteStatus.EXPIRED, valid_until=PAST, now=NOW) == {QuoteAction.REVISE}
    with pytest.raises(InvalidTransitionError, match="cannot expire a quote that is expired"):
        targets(QuoteStatus.EXPIRED, QuoteAction.EXPIRE, valid_until=PAST, now=NOW)


@pytest.mark.parametrize("status", [QuoteStatus.DRAFT, QuoteStatus.REJECTED, QuoteStatus.ACCEPTED])
def test_quote_lifecycle_drafts_rejections_and_acceptances_never_expire(
    status: QuoteStatus,
) -> None:
    assert effective_status(status, PAST, NOW) is status


def test_quote_lifecycle_accepted_quote_converts_after_its_date() -> None:
    # The customer accepted in time; fulfilling the order later is not acting on the offer.
    reached = targets(QuoteStatus.ACCEPTED, QuoteAction.CONVERT, valid_until=PAST, now=NOW)

    assert reached == {QuoteStatus.CONVERTED}


def reachable_from(start: QuoteStatus) -> set[QuoteStatus]:
    seen, queue = {start}, deque([start])
    while queue:
        for reached in TRANSITIONS[queue.popleft()].values():
            for status in reached - seen:
                seen.add(status)
                queue.append(status)
    return seen


def test_quote_lifecycle_reaches_every_status_from_draft() -> None:
    assert reachable_from(QuoteStatus.DRAFT) == set(QuoteStatus)


@pytest.mark.parametrize("status", sorted(set(QuoteStatus) - TERMINAL))
def test_quote_lifecycle_has_no_dead_ends(status: QuoteStatus) -> None:
    assert reachable_from(status) & TERMINAL


def test_quote_lifecycle_table_names_every_status_and_only_known_targets() -> None:
    assert set(TRANSITIONS) == set(QuoteStatus)
    assert all(
        reached and reached <= set(QuoteStatus)
        for actions in TRANSITIONS.values()
        for reached in actions.values()
    )


days = st.integers(min_value=-60, max_value=60)
# Any second of two years, timezone-aware as every time in the domain.
moments = st.integers(min_value=0, max_value=2 * 365 * 86_400).map(
    lambda seconds: datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)
)


@given(data=st.data(), today=moments, offsets=st.lists(days, max_size=40))
def test_quote_lifecycle_no_sequence_reaches_an_unknown_status_or_leaves_a_terminal_one(
    data: st.DataObject, today: datetime, offsets: list[int]
) -> None:
    status = QuoteStatus.DRAFT
    for offset in offsets:
        action = data.draw(st.sampled_from(QuoteAction))
        valid_until = today.date() + timedelta(days=offset)
        before = status
        try:
            reached = targets(status, action, valid_until=valid_until, now=today)
        except ConflictError:
            assert status is before
            continue
        assert before not in TERMINAL
        assert action in allowed_actions(before, valid_until=valid_until, now=today)
        status = data.draw(st.sampled_from(sorted(reached)))
        assert status in set(QuoteStatus)


def outcome(status: QuoteStatus, action: QuoteAction, valid_until: date, now: datetime) -> object:
    try:
        return targets(status, action, valid_until=valid_until, now=now)
    except ConflictError as error:
        return type(error)


@given(
    status=st.sampled_from(sorted(EXPIRABLE)),
    action=st.sampled_from(QuoteAction),
    now=moments,
    days_late=st.integers(min_value=1, max_value=400),
)
def test_quote_lifecycle_expired_offer_acts_as_if_the_job_had_already_run(
    status: QuoteStatus, action: QuoteAction, now: datetime, days_late: int
) -> None:
    valid_until = now.date() - timedelta(days=days_late)

    before_the_job = outcome(status, action, valid_until, now)

    if action is QuoteAction.EXPIRE:
        assert before_the_job == {QuoteStatus.EXPIRED}
    else:
        after_the_job = outcome(QuoteStatus.EXPIRED, action, valid_until, now)
        allowed = isinstance(after_the_job, frozenset)
        assert (
            (before_the_job == after_the_job)
            if allowed
            else not isinstance(before_the_job, frozenset)
        )
    assert allowed_actions(status, valid_until=valid_until, now=now) == {
        QuoteAction.REVISE,
        QuoteAction.EXPIRE,
    }
