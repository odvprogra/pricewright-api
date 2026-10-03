"""The tenant's audit trail (admins: ``audit:read``, ADR-0013)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.api.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, encode_cursor
from pricewright.application.audit import list_audit_events
from pricewright.application.ports import AuditEventFilter
from pricewright.domain.audit import (
    ActorType,
    AuditAction,
    AuditEvent,
    AuditResourceType,
    AuditValue,
)

router = APIRouter(prefix="/api/v1/audit-events", tags=["audit"])


class Change(BaseModel):
    model_config = ConfigDict(frozen=True)

    before: AuditValue
    after: AuditValue


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    occurred_at: datetime
    actor_type: ActorType
    actor_id: UUID = Field(description="The user's or the service account's id.")
    # Open-ended (ADR-0015): new actions and resource types appear as the API grows.
    action: str = Field(
        description="`resource.verb`. New values may appear; handle unknown ones.",
        examples=list(AuditAction),
    )
    resource_type: str = Field(
        description="New values may appear; handle unknown ones.",
        examples=list(AuditResourceType),
    )
    resource_id: UUID
    changes: dict[str, Change] = Field(
        description="Only the fields that changed. Decimals, ids and times are strings."
    )
    request_id: str | None = Field(
        description="The `X-Request-ID` of the API call that made the change."
    )

    @classmethod
    def of(cls, event: AuditEvent) -> AuditEventResponse:
        return cls(
            id=event.id,
            occurred_at=event.occurred_at,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            action=event.action.value,
            resource_type=event.resource_type.value,
            resource_id=event.resource_id,
            changes={
                name: Change(before=before, after=after)
                for name, (before, after) in event.changes.items()
            },
            request_id=event.request_id,
        )


class AuditEventPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[AuditEventResponse]
    next_cursor: str | None


@router.get(
    "",
    summary="The tenant's audit trail, newest first",
    responses={
        401: {"description": "Missing or invalid credentials"},
        403: {"description": "Only admins read the audit trail (`audit:read`)"},
    },
)
async def read_audit_events(
    principal: PrincipalDep,
    services: ServicesDep,
    resource_type: Annotated[AuditResourceType | None, Query()] = None,
    resource_id: Annotated[UUID | None, Query()] = None,
    actor_id: Annotated[UUID | None, Query(description="A user's or service account's id")] = None,
    action: Annotated[AuditAction | None, Query()] = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> AuditEventPage:
    where = AuditEventFilter(
        resource_type=resource_type, resource_id=resource_id, actor_id=actor_id, action=action
    )
    query = {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "actor_id": actor_id,
        "action": action,
    }
    page = await list_audit_events(
        principal,
        where,
        after=decode_cursor(cursor, query),
        limit=limit,
        unit_of_work=services.unit_of_work,
    )
    return AuditEventPage(
        items=[AuditEventResponse.of(event) for event in page.items],
        next_cursor=encode_cursor(page.next_after, query),
    )
