"""The approval inbox (``quotes:read``): approval requests with the quotes they are about,
oldest first (ADR-0020). Deciding is ``POST /api/v1/quotes/{quote_id}/approve`` or ``/reject``."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.api.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, encode_cursor
from pricewright.api.quotes import ApprovalRequestJson, QuoteSummaryJson
from pricewright.application.ports import ApprovalSummary
from pricewright.application.quotes import list_approval_requests
from pricewright.domain.quote_approvals import ApprovalStatus

router = APIRouter(prefix="/api/v1/approval-requests", tags=["quotes"])


class ApprovalInboxItem(ApprovalRequestJson):
    quote: QuoteSummaryJson

    @classmethod
    def from_summary(cls, item: ApprovalSummary) -> ApprovalInboxItem:
        request = ApprovalRequestJson.of(item.request)
        return cls(**request.model_dump(), quote=QuoteSummaryJson.of(item.quote))


class ApprovalPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[ApprovalInboxItem]
    next_cursor: str | None


@router.get(
    "",
    summary="The approval inbox: requests waiting for a decision, oldest first",
    responses={
        401: {"description": "Missing or invalid credentials"},
        403: {"description": "Needs `quotes:read`"},
    },
)
async def read_approval_requests(
    principal: PrincipalDep,
    services: ServicesDep,
    status: Annotated[
        ApprovalStatus,
        Query(
            description="Pending by default; a pending request leaves the inbox when its offer "
            "expires, since the quote can then only be revised."
        ),
    ] = ApprovalStatus.PENDING,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> ApprovalPage:
    fingerprint = {"status": status}
    page = await list_approval_requests(
        principal,
        status,
        after=decode_cursor(cursor, fingerprint),
        limit=limit,
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    return ApprovalPage(
        items=[ApprovalInboxItem.from_summary(item) for item in page.items],
        next_cursor=encode_cursor(page.next_after, fingerprint),
    )
