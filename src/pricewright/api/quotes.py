"""Quotes (``quotes:read`` / ``quotes:manage``): drafts priced by the engine and explained step by
step (ADR-0019); costs and margins only reach people (ADR-0017)."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from http import HTTPStatus
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from pricewright.api.concurrency import etag, expected_version
from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.api.idempotency import IdempotencyKey, idempotent_request, mark_replayed
from pricewright.api.money import MoneyJson
from pricewright.api.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, encode_cursor
from pricewright.api.pricing import MarginFloorJson, MarginJson, StepJson, ratio
from pricewright.application.ports import QuoteQuery, QuoteSort, QuoteSummary
from pricewright.application.quotes import (
    LineEdit,
    NewQuote,
    QuoteTerms,
    accept_quote,
    add_quote_line,
    approve_quote,
    cancel_quote,
    change_quote_line,
    change_quote_terms,
    create_quote,
    get_quote,
    list_quotes,
    recall_quote,
    reject_quote,
    remove_quote_line,
    revise_quote,
    send_quote,
    submit_quote,
)
from pricewright.domain.actors import Actor
from pricewright.domain.audit import ActorType
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.catalog import UnitOfMeasure
from pricewright.domain.pricing import (
    MAX_REASON_LENGTH,
    ApprovalReason,
    ManualOverride,
    PriceOverride,
    RateOverride,
)
from pricewright.domain.quantities import QUANTITY_DECIMAL_PLACES
from pricewright.domain.quote_approvals import (
    MAX_COMMENT_LENGTH,
    ApprovalRequest,
    ApprovalStatus,
)
from pricewright.domain.quote_lifecycle import QuoteAction, QuoteStatus
from pricewright.domain.quotes import (
    MAX_NOTES_LENGTH,
    MAX_QUOTE_LINES,
    LineChange,
    Quote,
    QuoteLine,
)
from pricewright.domain.quotes import (
    MAX_REASON_LENGTH as MAX_CANCEL_REASON_LENGTH,
)
from pricewright.domain.updates import KEEP

router = APIRouter(prefix="/api/v1/quotes", tags=["quotes"])

_QUANTITY_PLACES = Decimal(1).scaleb(-QUANTITY_DECIMAL_PLACES)
# What a client can ask for; the job that persists expiry (M5) is not a client action.
_CLIENT_ACTIONS = [action for action in QuoteAction if action is not QuoteAction.EXPIRE]

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"description": "Missing or invalid credentials"},
    403: {"description": "Needs `quotes:read`, or `quotes:manage` for changes"},
}
_NOT_FOUND: dict[int | str, dict[str, object]] = {
    404: {"description": "No such quote in this tenant (ADR-0009)"}
}
_CONDITIONAL: dict[int | str, dict[str, object]] = {
    412: {"description": "If-Match is stale: someone else changed the quote; reload it"},
    428: {"description": "If-Match is required"},
}
_NUMBER_LENGTH = 24
_LINE_ERRORS: dict[int | str, dict[str, object]] = {
    403: {"description": "Needs `quotes:manage`, and `quotes:override` for overrides"},
    404: {"description": "No such quote, or no such line on it (ADR-0009)"},
    409: {"description": "Only drafts change (`quote_not_editable`)"},
    422: {"description": "Invalid line or override, or an unknown or archived product"},
}


class ActorJson(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: ActorType
    id: UUID

    @classmethod
    def of(cls, actor: Actor) -> ActorJson:
        return cls(type=actor.type, id=actor.id)


class OverrideJson(BaseModel):
    """A manager's manual override, the last step of the waterfall (brief §4, rule 1)."""

    model_config = ConfigDict(frozen=True)

    rate: Decimal | None = Field(description="The rate off; null when it sets the unit price.")
    unit_price: MoneyJson | None = Field(description="The net unit price it sets, if it does.")
    reason: str
    set_by: UUID | None = Field(description="The person who set it.")


class QuoteLineJson(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    product_id: UUID
    sku: str = Field(description="As the product was when the line was last priced.")
    product_name: str
    # Open-ended (ADR-0015): units may be added.
    unit: str = Field(
        description="UN/ECE Recommendation 20 code. New units may appear; handle unknown ones.",
        examples=list(UnitOfMeasure),
    )
    quantity: Decimal
    list_unit_price: MoneyJson
    steps: list[StepJson] = Field(
        description="The waterfall, in order; stages that did not apply are left out."
    )
    net_unit_price: MoneyJson = Field(description="Rounded to 4 places at each step, half up.")
    list_total: MoneyJson
    net_total: MoneyJson = Field(description="Rounded to the currency's minor units, half up.")
    # Left out, not null, for callers without costs:read (ADR-0017).
    margin: MarginJson | SkipJsonSchema[None] = Field(
        default=None,
        exclude_if=lambda margin: margin is None,
        description="Only for people with `costs:read`; service accounts never see it.",
    )
    margin_floor: MarginFloorJson | None = Field(description="The floor that applies, if any.")
    below_margin_floor: bool = Field(description="True when the line needs approval for it.")
    override: OverrideJson | None
    added_by: ActorJson

    @classmethod
    def of(cls, line: QuoteLine, caller: Principal) -> QuoteLineJson:
        pricing, floor, override = line.pricing, line.pricing.margin_floor, line.override
        margin_rate = pricing.margin_rate
        return cls(
            id=line.id,
            product_id=line.product_id,
            sku=line.sku,
            product_name=line.product_name,
            unit=line.unit.value,
            quantity=line.quantity.quantize(_QUANTITY_PLACES),
            list_unit_price=MoneyJson.of(pricing.breakdown.list_unit_price),
            steps=[StepJson.of(step) for step in pricing.breakdown.steps],
            net_unit_price=MoneyJson.of(pricing.breakdown.net_unit_price),
            list_total=MoneyJson.of(pricing.list_total),
            net_total=MoneyJson.of(pricing.net_total),
            margin=MarginJson(
                amount=MoneyJson.of(pricing.margin),
                rate=None if margin_rate is None else ratio(margin_rate),
            )
            if caller.holds(Permission.COSTS_READ)
            else None,
            margin_floor=None
            if floor is None
            else MarginFloorJson(rule_id=floor.rule_id, label=floor.label, rate=ratio(floor.rate)),
            below_margin_floor=pricing.below_margin_floor,
            override=None
            if override is None
            else OverrideJson(
                rate=ratio(override.rate) if isinstance(override, RateOverride) else None,
                unit_price=MoneyJson.of(override.unit_price)
                if isinstance(override, PriceOverride)
                else None,
                reason=override.reason,
                set_by=line.override_by,
            ),
            added_by=ActorJson.of(line.added_by),
        )


class ApprovalRequestJson(BaseModel):
    """What was asked when the quote was submitted, and what was decided (ADR-0020)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    # Open-ended (ADR-0015): the expiry job (M5) may close requests in a new way.
    status: str = Field(
        description="New statuses may appear; handle unknown ones.", examples=list(ApprovalStatus)
    )
    requested_by: ActorJson
    requested_at: datetime
    reasons: list[str] = Field(
        description="New reasons may appear; handle unknown ones.", examples=[list(ApprovalReason)]
    )
    discount: Decimal = Field(description="1 - net / list subtotal when submitted (D-06).")
    approval_threshold: Decimal
    list_subtotal: MoneyJson
    net_subtotal: MoneyJson
    decided_by: UUID | None = Field(description="The person who approved or rejected it.")
    decided_at: datetime | None = Field(description="When it was decided or withdrawn.")
    comment: str | None

    @classmethod
    def of(cls, request: ApprovalRequest) -> ApprovalRequestJson:
        return cls(
            id=request.id,
            status=request.status.value,
            requested_by=ActorJson.of(request.requested_by),
            requested_at=request.requested_at,
            reasons=[reason.value for reason in request.reasons],
            discount=ratio(request.discount),
            approval_threshold=ratio(request.approval_threshold),
            list_subtotal=MoneyJson.of(request.list_subtotal),
            net_subtotal=MoneyJson.of(request.net_subtotal),
            decided_by=request.decided_by,
            decided_at=request.decided_at,
            comment=request.comment,
        )


class QuoteResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    number: str = Field(
        description="`NF-2026-000123`; later revisions add `-R2`, `-R3`.",
        examples=["NF-2026-000123-R2"],
    )
    revision: int
    customer_id: UUID
    status: QuoteStatus
    valid_until: date = Field(description="Valid through the end of this day, UTC.")
    notes: str | None
    lines: list[QuoteLineJson]
    list_subtotal: MoneyJson
    net_subtotal: MoneyJson = Field(description="The sum of the line totals.")
    tax_rate: Decimal
    tax: MoneyJson = Field(description="Computed once on the net subtotal, rounded half up.")
    total: MoneyJson
    discount: Decimal = Field(
        description="1 - net subtotal / list subtotal, before tax: what the quote gives away."
    )
    approval_threshold: Decimal
    requires_approval: bool
    # Open-ended (ADR-0015): reasons may be added.
    approval_reasons: list[str] = Field(
        description="Why approval is needed. New reasons may appear; handle unknown ones.",
        examples=[list(ApprovalReason)],
    )
    priced_at: datetime = Field(description="When the lines were last priced; frozen at submit.")
    # Open-ended (ADR-0015): actions arrive with later milestones (orders, M6).
    allowed_actions: list[str] = Field(
        description="What can be done with the quote now. New actions may appear.",
        examples=[_CLIENT_ACTIONS],
    )
    created_by: ActorJson
    created_at: datetime
    status_changed_at: datetime | None
    submitted_by: ActorJson | None
    submitted_at: datetime | None
    cancel_reason: str | None
    supersedes_id: UUID | None = Field(description="The revision this one replaced.")
    superseded_by_id: UUID | None = Field(description="The revision that replaced this one.")
    approvals: list[ApprovalRequestJson] = Field(
        description="This revision's approval requests, oldest first; at most one pending."
    )
    version: int = Field(description="Also sent as the ETag; send it back in If-Match to change.")

    @classmethod
    def of(cls, quote: Quote, caller: Principal, now: datetime) -> QuoteResponse:
        totals, submitted_by = quote.totals, quote.submitted_by
        return cls(
            id=quote.id,
            number=quote.display_number,
            revision=quote.revision,
            customer_id=quote.customer_id,
            status=quote.status,
            valid_until=quote.valid_until,
            notes=quote.notes,
            lines=[QuoteLineJson.of(line, caller) for line in quote.lines],
            list_subtotal=MoneyJson.of(totals.list_subtotal),
            net_subtotal=MoneyJson.of(totals.net_subtotal),
            tax_rate=ratio(totals.tax_rate),
            tax=MoneyJson.of(totals.tax),
            total=MoneyJson.of(totals.total),
            discount=quote.discount,
            approval_threshold=ratio(totals.approval_threshold),
            requires_approval=bool(quote.approval_reasons),
            approval_reasons=[reason.value for reason in quote.approval_reasons],
            priced_at=totals.priced_at,
            allowed_actions=sorted(
                action.value for action in quote.allowed_actions(now) if action in _CLIENT_ACTIONS
            ),
            created_by=ActorJson.of(quote.created_by),
            created_at=quote.created_at,
            status_changed_at=quote.status_changed_at,
            submitted_by=None if submitted_by is None else ActorJson.of(submitted_by),
            submitted_at=quote.submitted_at,
            cancel_reason=quote.cancel_reason,
            supersedes_id=quote.supersedes_id,
            superseded_by_id=quote.superseded_by_id,
            approvals=[ApprovalRequestJson.of(request) for request in quote.approvals],
            version=quote.version,
        )


class OverrideRequest(BaseModel):
    """A manual override, with a reason: a rate off what the rules left, or the net unit price.
    Needs `quotes:override` (sales managers and admins)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rate: Decimal | None = Field(
        default=None, description="More than 0 and at most 1, up to 4 places.", examples=["0.1"]
    )
    unit_price: MoneyJson | None = Field(default=None, description="The net unit price itself.")
    reason: str = Field(min_length=1, max_length=MAX_REASON_LENGTH, examples=["Matching a bid"])

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        if (self.rate is None) == (self.unit_price is None):
            raise ValueError("send either rate or unit_price")
        return self

    def to_override(self) -> ManualOverride:
        if self.unit_price is not None:
            return PriceOverride(self.unit_price.to_money(), self.reason)
        return RateOverride(self.rate or Decimal(0), self.reason)


_QUANTITY = "More than 0, up to 3 decimal places."


class NewLineJson(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    product_id: UUID
    quantity: Decimal = Field(description=_QUANTITY, examples=["10"])
    override: OverrideRequest | None = None

    def change(self) -> LineChange:
        override = None if self.override is None else self.override.to_override()
        return LineChange(self.product_id, self.quantity, override)


class CreateQuoteRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    customer_id: UUID
    valid_until: date | None = Field(
        default=None,
        description="Today or later; defaults to today plus the tenant's `quote_validity_days`.",
    )
    notes: str | None = Field(default=None, max_length=MAX_NOTES_LENGTH)
    lines: list[NewLineJson] = Field(default_factory=list, max_length=MAX_QUOTE_LINES)


def _respond(response: Response, quote: Quote, caller: Principal, now: datetime) -> QuoteResponse:
    response.headers["ETag"] = etag(quote.version)
    return QuoteResponse.of(quote, caller, now)


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    summary="Start a draft quote, optionally with its first lines, priced by the engine",
    responses=_ERRORS
    | {
        409: {"description": "A request with this Idempotency-Key is still running"},
        422: {
            "description": "Invalid quote, an unknown or archived customer or product of this "
            "tenant, or an Idempotency-Key that is invalid or was used for a different request"
        },
    },
)
async def add_quote(
    body: CreateQuoteRequest,
    principal: PrincipalDep,
    services: ServicesDep,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> QuoteResponse:
    created = await create_quote(
        principal,
        NewQuote(
            customer_id=body.customer_id,
            valid_until=body.valid_until,
            notes=body.notes,
            lines=[line.change() for line in body.lines],
        ),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
        idempotency=idempotent_request(idempotency_key, request, body),
    )
    quote = created.value
    response.headers["Location"] = f"{router.prefix}/{quote.id}"
    mark_replayed(response, created.replayed)
    return _respond(response, quote, principal, services.clock())


@router.get("/{quote_id}", summary="One quote, line by line", responses=_ERRORS | _NOT_FOUND)
async def read_quote(
    quote_id: UUID, principal: PrincipalDep, services: ServicesDep, response: Response
) -> QuoteResponse:
    quote = await get_quote(principal, quote_id, unit_of_work=services.unit_of_work)
    return _respond(response, quote, principal, services.clock())


class QuoteSummaryJson(BaseModel):
    """A quote as lists show it: no lines."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    number: str = Field(examples=["NF-2026-000123-R2"])
    revision: int
    customer_id: UUID
    status: QuoteStatus
    valid_until: date
    net_subtotal: MoneyJson
    total: MoneyJson
    created_by: ActorJson
    created_at: datetime
    status_changed_at: datetime | None
    version: int

    @classmethod
    def of(cls, quote: QuoteSummary) -> QuoteSummaryJson:
        return cls(
            id=quote.id,
            number=quote.display_number,
            revision=quote.revision,
            customer_id=quote.customer_id,
            status=quote.status,
            valid_until=quote.valid_until,
            net_subtotal=MoneyJson.of(quote.net_subtotal),
            total=MoneyJson.of(quote.total),
            created_by=ActorJson.of(quote.created_by),
            created_at=quote.created_at,
            status_changed_at=quote.status_changed_at,
            version=quote.version,
        )


class QuotePage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[QuoteSummaryJson]
    next_cursor: str | None


class QuoteOrder(StrEnum):
    NEWEST = "-created_at"
    OLDEST = "created_at"
    EXPIRING_FIRST = "valid_until"
    EXPIRING_LAST = "-valid_until"


@router.get("", summary="List quotes, newest first by default", responses=_ERRORS)
async def read_quotes(
    principal: PrincipalDep,
    services: ServicesDep,
    status: QuoteStatus | None = None,
    customer_id: UUID | None = None,
    number: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=_NUMBER_LENGTH,
            description="Every revision of this number, written without `-R2`.",
            examples=["NF-2026-000123"],
        ),
    ] = None,
    created_by: Annotated[
        UUID | None, Query(description="The user or service account that created it.")
    ] = None,
    sort: Annotated[QuoteOrder, Query(description="`-` sorts descending.")] = QuoteOrder.NEWEST,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> QuotePage:
    query = QuoteQuery(
        status=status,
        customer_id=customer_id,
        number=number,
        created_by=created_by,
        sort=QuoteSort(sort.removeprefix("-")),
        descending=sort.startswith("-"),
    )
    fingerprint = {
        "status": status,
        "customer_id": None if customer_id is None else str(customer_id),
        "number": number,
        "created_by": None if created_by is None else str(created_by),
        "sort": sort,
    }
    page = await list_quotes(
        principal,
        query,
        after=decode_cursor(cursor, fingerprint),
        limit=limit,
        unit_of_work=services.unit_of_work,
    )
    return QuotePage(
        items=[QuoteSummaryJson.of(quote) for quote in page.items],
        next_cursor=encode_cursor(page.next_after, fingerprint),
    )


class QuoteTermsPatch(BaseModel):
    """Only the fields sent change; `notes: null` removes them. Only drafts change."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid_until: date | None = Field(default=None, description="Today or later.")
    notes: str | None = Field(default=None, max_length=MAX_NOTES_LENGTH)

    @model_validator(mode="after")
    def _changes_something(self) -> Self:
        if self.model_fields_set == set():
            raise ValueError("send at least one field to change")
        return self

    def terms(self) -> QuoteTerms:
        return QuoteTerms(
            valid_until=self.valid_until,
            notes=self.notes if "notes" in self.model_fields_set else KEEP,
        )


@router.patch(
    "/{quote_id}",
    summary="Change a draft's validity or notes",
    responses=_ERRORS
    | _NOT_FOUND
    | _CONDITIONAL
    | {409: {"description": "Only drafts change (`quote_not_editable`)"}},
)
async def update_quote(
    quote_id: UUID,
    body: QuoteTermsPatch,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> QuoteResponse:
    quote = await change_quote_terms(
        principal,
        quote_id,
        body.terms(),
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    return _respond(response, quote, principal, services.clock())


@router.post(
    "/{quote_id}/lines",
    summary="Add a line to a draft; every line is priced again",
    description="Returns the quote with its new ETag; the new line is the last one.",
    responses=_ERRORS | _CONDITIONAL | _LINE_ERRORS,
)
async def add_line(
    quote_id: UUID,
    body: NewLineJson,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> QuoteResponse:
    quote = await add_quote_line(
        principal,
        quote_id,
        body.change(),
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    return _respond(response, quote, principal, services.clock())


class LinePatch(BaseModel):
    """Only the fields sent change; `override: null` removes the override."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    quantity: Decimal | None = Field(default=None, description=_QUANTITY)
    override: OverrideRequest | None = None

    @model_validator(mode="after")
    def _changes_something(self) -> Self:
        if self.model_fields_set == set():
            raise ValueError("send at least one field to change")
        return self

    def edit(self) -> LineEdit:
        if "override" not in self.model_fields_set:
            return LineEdit(quantity=self.quantity)
        override = None if self.override is None else self.override.to_override()
        return LineEdit(quantity=self.quantity, override=override)


@router.patch(
    "/{quote_id}/lines/{line_id}",
    summary="Change a line's quantity or override; every line is priced again",
    responses=_ERRORS | _CONDITIONAL | _LINE_ERRORS,
)
async def update_line(
    quote_id: UUID,
    line_id: UUID,
    body: LinePatch,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> QuoteResponse:
    quote = await change_quote_line(
        principal,
        quote_id,
        line_id,
        body.edit(),
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    return _respond(response, quote, principal, services.clock())


@router.delete(
    "/{quote_id}/lines/{line_id}",
    summary="Remove a line from a draft; the rest are priced again",
    description="Returns the quote with its new ETag.",
    responses=_ERRORS | _CONDITIONAL | _LINE_ERRORS,
)
async def delete_line(
    quote_id: UUID,
    line_id: UUID,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> QuoteResponse:
    quote = await remove_quote_line(
        principal,
        quote_id,
        line_id,
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    return _respond(response, quote, principal, services.clock())


_TRANSITION_ERRORS: dict[int | str, dict[str, object]] = {
    409: {
        "description": "The quote's status does not allow it (`invalid_transition`), its validity "
        "passed (`quote_expired`), or it has no lines (`quote_empty`)"
    },
}
_TRANSITIONS = _ERRORS | _NOT_FOUND | _CONDITIONAL | _TRANSITION_ERRORS
IfMatch = Annotated[str | None, Header(alias="If-Match")]


@router.post(
    "/{quote_id}/submit",
    summary="Submit a draft: priced one last time, then approved or sent for approval",
    responses=_TRANSITIONS
    | {422: {"description": "A product or the customer was archived since the last pricing"}},
)
async def submit(
    quote_id: UUID,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: IfMatch = None,
) -> QuoteResponse:
    quote = await submit_quote(
        principal,
        quote_id,
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    return _respond(response, quote, principal, services.clock())


@router.post(
    "/{quote_id}/recall",
    summary="Withdraw a pending approval request; the quote is a draft again",
    responses=_TRANSITIONS,
)
async def recall(
    quote_id: UUID,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: IfMatch = None,
) -> QuoteResponse:
    quote = await recall_quote(
        principal,
        quote_id,
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    return _respond(response, quote, principal, services.clock())


@router.post(
    "/{quote_id}/send",
    summary="Mark an approved quote as sent to the customer (people only)",
    responses=_TRANSITIONS | {403: {"description": "Needs `quotes:send`; never integrations"}},
)
async def send(
    quote_id: UUID,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: IfMatch = None,
) -> QuoteResponse:
    quote = await send_quote(
        principal,
        quote_id,
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    return _respond(response, quote, principal, services.clock())


@router.post(
    "/{quote_id}/accept",
    summary="Record the customer's acceptance of a sent quote (people only)",
    responses=_TRANSITIONS | {403: {"description": "Needs `quotes:send`; never integrations"}},
)
async def accept(
    quote_id: UUID,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: IfMatch = None,
) -> QuoteResponse:
    quote = await accept_quote(
        principal,
        quote_id,
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    return _respond(response, quote, principal, services.clock())


class CancelRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(
        min_length=1, max_length=MAX_CANCEL_REASON_LENGTH, examples=["Customer chose another bid"]
    )


@router.post(
    "/{quote_id}/cancel",
    summary="Close an open quote for good, with a reason (also a customer's no)",
    responses=_TRANSITIONS,
)
async def cancel(
    quote_id: UUID,
    body: CancelRequest,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: IfMatch = None,
) -> QuoteResponse:
    quote = await cancel_quote(
        principal,
        quote_id,
        body.reason,
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    return _respond(response, quote, principal, services.clock())


@router.post(
    "/{quote_id}/revise",
    status_code=HTTPStatus.CREATED,
    summary="Supersede the quote with its next revision, a draft priced now",
    description="Returns the new revision; the old one becomes `superseded` and links to it.",
    responses=_TRANSITIONS
    | {422: {"description": "A product or the customer was archived since the last pricing"}},
)
async def revise(
    quote_id: UUID,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: IfMatch = None,
) -> QuoteResponse:
    successor = await revise_quote(
        principal,
        quote_id,
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    response.headers["Location"] = f"{router.prefix}/{successor.id}"
    return _respond(response, successor, principal, services.clock())


class ApproveRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    comment: str | None = Field(
        default=None, max_length=MAX_COMMENT_LENGTH, examples=["Strategic account"]
    )


class RejectRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    comment: str = Field(
        min_length=1,
        max_length=MAX_COMMENT_LENGTH,
        description="What to change before the quote is submitted again.",
        examples=["Keep the bolts above a 20% margin."],
    )


_DECISIONS = _TRANSITIONS | {
    403: {
        "description": "Needs `quotes:approve` (`permission_denied`), or the caller built or "
        "submitted the quote (`self_approval`, ADR-0020)"
    }
}


@router.post(
    "/{quote_id}/approve",
    summary="Approve a quote pending approval (someone who did not build it)",
    responses=_DECISIONS,
)
async def approve(
    quote_id: UUID,
    body: ApproveRequest,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: IfMatch = None,
) -> QuoteResponse:
    quote = await approve_quote(
        principal,
        quote_id,
        body.comment,
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    return _respond(response, quote, principal, services.clock())


@router.post(
    "/{quote_id}/reject",
    summary="Reject a quote pending approval, saying what to change",
    responses=_DECISIONS,
)
async def reject(
    quote_id: UUID,
    body: RejectRequest,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: IfMatch = None,
) -> QuoteResponse:
    quote = await reject_quote(
        principal,
        quote_id,
        body.comment,
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    return _respond(response, quote, principal, services.clock())
