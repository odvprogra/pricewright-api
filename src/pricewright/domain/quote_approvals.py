"""Approval requests: a manager's decision on a submitted quote (ADR-0020).

A request records what was asked (the reasons and the metric of decision D-06, as the quote stood
when submitted) and what was decided, by whom and why. The person who decides never built the
revision or asked for its approval: segregation of duties, the four-eyes principle.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pricewright.domain.actors import Actor
from pricewright.domain.auth import PermissionDeniedError
from pricewright.domain.errors import RuleViolationError
from pricewright.domain.money import Money
from pricewright.domain.pricing import ApprovalReason

MAX_COMMENT_LENGTH = 1000


class InvalidDecisionError(RuleViolationError):
    code = "invalid_decision"


class SelfApprovalError(PermissionDeniedError):
    """Raised after the lookup on purpose: the quote is the caller's tenant's own (ADR-0009)."""

    code = "self_approval"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    """Closed undecided: the quote was recalled, cancelled or revised."""


def comment_text(comment: str | None, *, required: bool) -> str | None:
    text = None if comment is None else comment.strip() or None
    if text is None and required:
        raise InvalidDecisionError("a rejection needs a comment saying what to change")
    if text is not None and len(text) > MAX_COMMENT_LENGTH:
        raise InvalidDecisionError(f"a comment has at most {MAX_COMMENT_LENGTH} characters")
    return text


@dataclass(slots=True)
class ApprovalRequest:
    id: uuid.UUID
    requested_by: Actor
    requested_at: datetime
    reasons: tuple[ApprovalReason, ...]
    discount: Decimal
    """The value-weighted discount (D-06) when the quote was submitted."""
    approval_threshold: Decimal
    list_subtotal: Money
    net_subtotal: Money
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: uuid.UUID | None = None
    """The person who approved or rejected it."""
    decided_at: datetime | None = None
    comment: str | None = None

    def decide(
        self, status: ApprovalStatus, *, by: Actor, comment: str | None, now: datetime
    ) -> None:
        if status not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
            raise ValueError("a decision approves or rejects")
        text = comment_text(comment, required=status is ApprovalStatus.REJECTED)
        self.status, self.decided_by, self.decided_at, self.comment = status, by.id, now, text

    def withdraw(self, now: datetime) -> None:
        self.status, self.decided_at = ApprovalStatus.WITHDRAWN, now
