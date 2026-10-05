"""Audit events: who changed what and when, with the values before and after (ADR-0013).

A use case appends its event in the same unit of work as the change it records, so a change is
never saved without its event, nor an event without its change. Events are never updated or deleted.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from pricewright.domain.auth import Principal

type AuditValue = str | int | bool | None
"""A field's value as JSON keeps it: callers turn decimals, ids and times into strings."""
type Changes = Mapping[str, tuple[AuditValue, AuditValue]]
"""The fields that changed: name → (before, after). Never secrets or their digests."""


class ActorType(StrEnum):
    USER = "user"
    SERVICE_ACCOUNT = "service_account"


class AuditResourceType(StrEnum):
    TENANT = "tenant"
    USER = "user"
    SERVICE_ACCOUNT = "service_account"
    API_KEY = "api_key"
    PRODUCT_CATEGORY = "product_category"
    PRODUCT = "product"
    CUSTOMER = "customer"
    PRICING_RULE = "pricing_rule"
    QUOTE = "quote"


class AuditAction(StrEnum):
    """``resource.verb`` in the past tense, as GitHub and WorkOS name their audit events."""

    TENANT_UPDATED = "tenant.updated"
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_UNLOCKED = "user.unlocked"
    SERVICE_ACCOUNT_CREATED = "service_account.created"
    API_KEY_ISSUED = "api_key.issued"
    API_KEY_REVOKED = "api_key.revoked"
    PRODUCT_CATEGORY_CREATED = "product_category.created"
    PRODUCT_CATEGORY_UPDATED = "product_category.updated"
    PRODUCT_CREATED = "product.created"
    PRODUCT_UPDATED = "product.updated"
    CUSTOMER_CREATED = "customer.created"
    CUSTOMER_UPDATED = "customer.updated"
    PRICING_RULE_CREATED = "pricing_rule.created"
    PRICING_RULE_UPDATED = "pricing_rule.updated"
    QUOTE_CREATED = "quote.created"
    QUOTE_UPDATED = "quote.updated"
    QUOTE_LINE_ADDED = "quote.line_added"
    QUOTE_LINE_CHANGED = "quote.line_changed"
    QUOTE_LINE_REMOVED = "quote.line_removed"
    QUOTE_SUBMITTED = "quote.submitted"
    QUOTE_RECALLED = "quote.recalled"
    QUOTE_SENT = "quote.sent"
    QUOTE_ACCEPTED = "quote.accepted"
    QUOTE_CANCELLED = "quote.cancelled"
    QUOTE_REVISED = "quote.revised"
    QUOTE_APPROVED = "quote.approved"
    QUOTE_REJECTED = "quote.rejected"

    @property
    def resource_type(self) -> AuditResourceType:
        return AuditResourceType(self.value.partition(".")[0])


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: uuid.UUID
    tenant_id: uuid.UUID
    occurred_at: datetime
    actor_type: ActorType
    actor_id: uuid.UUID
    """The user's or the service account's id."""
    action: AuditAction
    resource_id: uuid.UUID
    changes: Changes = field(default_factory=dict)
    request_id: str | None = None
    """The API request behind it, stamped by the persistence adapter; None outside a request."""

    @property
    def resource_type(self) -> AuditResourceType:
        return self.action.resource_type

    @classmethod
    def record(
        cls,
        principal: Principal,
        action: AuditAction,
        resource_id: uuid.UUID,
        changes: Changes,
        *,
        now: datetime,
    ) -> AuditEvent:
        actor_type = ActorType.SERVICE_ACCOUNT if principal.is_service_account else ActorType.USER
        return cls(
            id=uuid.uuid7(),
            tenant_id=principal.tenant_id,
            occurred_at=now,
            actor_type=actor_type,
            actor_id=principal.subject_id,
            action=action,
            resource_id=resource_id,
            changes=dict(changes),
        )


def created(values: Mapping[str, AuditValue]) -> Changes:
    """A new record: every field with a first value goes from nothing to it."""
    return {name: (None, value) for name, value in values.items() if value is not None}


def removed(values: Mapping[str, AuditValue]) -> Changes:
    """A record gone: every field with a last value goes from it to nothing."""
    return {name: (value, None) for name, value in values.items() if value is not None}


def changed(before: Mapping[str, AuditValue], after: Mapping[str, AuditValue]) -> Changes:
    """Only the fields whose value differs between two snapshots with the same fields."""
    return {name: (before[name], value) for name, value in after.items() if before[name] != value}
