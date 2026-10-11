"""Events: what happened that other parts of the system react to, such as an email (ADR-0008).

A use case adds its event to the unit of work's outbox, next to the audit event, so both commit
with the change or not at all; the worker's relay delivers each one at least once, in order per
aggregate, so consumers must be idempotent. The envelope's fields follow CloudEvents 1.0 (``id``,
``type``, ``time``, ``data``, the aggregate as ``subject``), and the data is thin: consumers read
what they need when they run.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from pricewright.domain.actors import Actor
from pricewright.domain.audit import AuditResourceType
from pricewright.domain.quotes import Quote


class EventType(StrEnum):
    """``resource.verb`` in the past tense, like audit actions. New types come with a consumer."""

    QUOTE_APPROVED = "quote.approved"
    QUOTE_REJECTED = "quote.rejected"
    QUOTE_SENT = "quote.sent"

    @property
    def aggregate_type(self) -> AuditResourceType:
        return AuditResourceType(self.value.partition(".")[0])


@dataclass(frozen=True, slots=True)
class Event:
    id: uuid.UUID
    tenant_id: uuid.UUID
    type: EventType
    aggregate_id: uuid.UUID
    """The record the event is about; events of one aggregate are delivered in order."""
    occurred_at: datetime
    data: Mapping[str, str] = field(default_factory=dict)
    request_id: str | None = None
    """The API request behind it, stamped by the persistence adapter (ADR-0013's way)."""

    @property
    def aggregate_type(self) -> AuditResourceType:
        return self.type.aggregate_type


def quote_event(event_type: EventType, quote: Quote, by: Actor, *, now: datetime) -> Event:
    """What happened to a quote and who did it; consumers read the quote itself."""
    return Event(
        id=uuid.uuid7(),
        tenant_id=quote.tenant_id,
        type=event_type,
        aggregate_id=quote.id,
        occurred_at=now,
        data={"number": quote.display_number, "actor_type": by.type.value, "actor_id": str(by.id)},
    )
