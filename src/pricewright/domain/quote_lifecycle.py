"""The quote lifecycle as an explicit transition table (ADR-0005).

Each status names the actions it allows and where each one leads, as Dataverse's allowed status
transitions and SAP's status profiles declare them. The table is data: the quote aggregate looks
actions up in it, and tests and documentation walk it. Expiration needs no job to be correct: an
offer in flight whose ``valid_until`` has passed is treated as ``EXPIRED`` whether or not the
status was persisted yet.
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from enum import StrEnum
from types import MappingProxyType

from pricewright.domain.errors import ConflictError


class InvalidTransitionError(ConflictError):
    code = "invalid_transition"


class QuoteExpiredError(ConflictError):
    code = "quote_expired"


class QuoteStatus(StrEnum):
    DRAFT = "draft"
    """The only status whose lines and terms change."""
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SENT = "sent"
    ACCEPTED = "accepted"
    CONVERTED = "converted"
    """Became an order (M6)."""
    REJECTED = "rejected"
    """A manager refused the approval; revise or cancel it."""
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    """Replaced by a newer revision; it can never be accepted."""


class QuoteAction(StrEnum):
    SUBMIT = "submit"
    RECALL = "recall"
    """The submitter withdraws a pending approval request (Salesforce's recall)."""
    APPROVE = "approve"
    REJECT = "reject"
    SEND = "send"
    ACCEPT = "accept"
    CONVERT = "convert"
    CANCEL = "cancel"
    REVISE = "revise"
    """This revision is superseded by a new one in draft."""
    EXPIRE = "expire"
    """Persists what the clock already decided (the periodic job, M5)."""


_S = QuoteStatus
_A = QuoteAction

TRANSITIONS: Mapping[QuoteStatus, Mapping[QuoteAction, frozenset[QuoteStatus]]] = MappingProxyType(
    {
        # Submitting needs no approval when the quote gives nothing away beyond the threshold
        # and keeps every margin floor (brief §4, rule 3).
        _S.DRAFT: {
            _A.SUBMIT: frozenset({_S.PENDING_APPROVAL, _S.APPROVED}),
            _A.CANCEL: frozenset({_S.CANCELLED}),
        },
        _S.PENDING_APPROVAL: {
            _A.APPROVE: frozenset({_S.APPROVED}),
            _A.REJECT: frozenset({_S.REJECTED}),
            _A.RECALL: frozenset({_S.DRAFT}),
            _A.CANCEL: frozenset({_S.CANCELLED}),
            _A.EXPIRE: frozenset({_S.EXPIRED}),
        },
        _S.APPROVED: {
            _A.SEND: frozenset({_S.SENT}),
            _A.REVISE: frozenset({_S.SUPERSEDED}),
            _A.CANCEL: frozenset({_S.CANCELLED}),
            _A.EXPIRE: frozenset({_S.EXPIRED}),
        },
        _S.SENT: {
            _A.ACCEPT: frozenset({_S.ACCEPTED}),
            _A.REVISE: frozenset({_S.SUPERSEDED}),
            # The customer said no, or the offer is withdrawn (Stripe, Dynamics 365).
            _A.CANCEL: frozenset({_S.CANCELLED}),
            _A.EXPIRE: frozenset({_S.EXPIRED}),
        },
        _S.REJECTED: {
            _A.REVISE: frozenset({_S.SUPERSEDED}),
            _A.CANCEL: frozenset({_S.CANCELLED}),
        },
        _S.ACCEPTED: {_A.CONVERT: frozenset({_S.CONVERTED})},
        # A customer who comes back late gets a revision: same number, current prices.
        _S.EXPIRED: {_A.REVISE: frozenset({_S.SUPERSEDED})},
        _S.CONVERTED: {},
        _S.CANCELLED: {},
        _S.SUPERSEDED: {},
    }
)

TERMINAL = frozenset(status for status, actions in TRANSITIONS.items() if not actions)
EXPIRABLE = frozenset({_S.PENDING_APPROVAL, _S.APPROVED, _S.SENT})
"""Offers in flight. A draft is not an offer yet: submitting it needs a future ``valid_until``."""


def has_passed(valid_until: date, now: datetime) -> bool:
    """A quote is valid through the whole of its ``valid_until`` day, in UTC until tenants have
    time zones (ADR-0018)."""
    return now.astimezone(UTC).date() > valid_until


def effective_status(status: QuoteStatus, valid_until: date, now: datetime) -> QuoteStatus:
    """``EXPIRED`` for an offer in flight past its date, whether or not that was persisted yet."""
    if status in EXPIRABLE and has_passed(valid_until, now):
        return _S.EXPIRED
    return status


def allowed_actions(
    status: QuoteStatus, *, valid_until: date, now: datetime
) -> frozenset[QuoteAction]:
    """What can be done with the quote at ``now``.

    An expired offer allows what ``EXPIRED`` allows, plus persisting the expiry, so the answer is
    the same before and after the job persists the status (decision D-08).
    """
    if effective_status(status, valid_until, now) is _S.EXPIRED:
        return frozenset(TRANSITIONS[_S.EXPIRED]) | {_A.EXPIRE}
    return frozenset(TRANSITIONS[status]) - {_A.EXPIRE}


def targets(
    status: QuoteStatus, action: QuoteAction, *, valid_until: date, now: datetime
) -> frozenset[QuoteStatus]:
    """Where ``action`` may lead from ``status`` at ``now``.

    Raise ``QuoteExpiredError`` when only the expiry forbids the action, ``InvalidTransitionError``
    otherwise.
    """
    effective = effective_status(status, valid_until, now)
    if action in allowed_actions(status, valid_until=valid_until, now=now):
        return (
            TRANSITIONS[status][action] if action is _A.EXPIRE else TRANSITIONS[effective][action]
        )
    if action is _A.EXPIRE and status in EXPIRABLE:
        raise InvalidTransitionError(f"the quote is valid until {valid_until.isoformat()}")
    if effective is not status and action in TRANSITIONS[status]:
        raise QuoteExpiredError(f"the quote expired on {valid_until.isoformat()}; revise it")
    raise InvalidTransitionError(f"a {status.value} quote cannot {action.value}")
