"""Quotes (``quotes:read`` / ``quotes:manage``): drafts priced by the engine and explained step by
step (ADR-0019); costs and margins only reach people (ADR-0017)."""

from datetime import date, datetime
from decimal import Decimal
from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from pricewright.api.concurrency import etag
from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.api.money import MoneyJson
from pricewright.api.pricing import MarginFloorJson, MarginJson, StepJson, ratio
from pricewright.application.quotes import NewQuote, create_quote, get_quote
from pricewright.domain.actors import Actor
from pricewright.domain.audit import ActorType
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.catalog import UnitOfMeasure
from pricewright.domain.pricing import ApprovalReason, PriceOverride, RateOverride
from pricewright.domain.quantities import QUANTITY_DECIMAL_PLACES
from pricewright.domain.quote_lifecycle import QuoteAction, QuoteStatus
from pricewright.domain.quotes import (
    MAX_NOTES_LENGTH,
    MAX_QUOTE_LINES,
    LineChange,
    Quote,
    QuoteLine,
)

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
            version=quote.version,
        )


class NewLineJson(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    product_id: UUID
    quantity: Decimal = Field(description="More than 0, up to 3 decimal places.", examples=["10"])


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
        422: {
            "description": "Invalid quote, or an unknown or archived customer or product of this "
            "tenant"
        }
    },
)
async def add_quote(
    body: CreateQuoteRequest, principal: PrincipalDep, services: ServicesDep, response: Response
) -> QuoteResponse:
    quote = await create_quote(
        principal,
        NewQuote(
            customer_id=body.customer_id,
            valid_until=body.valid_until,
            notes=body.notes,
            lines=[LineChange(line.product_id, line.quantity) for line in body.lines],
        ),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    response.headers["Location"] = f"{router.prefix}/{quote.id}"
    return _respond(response, quote, principal, services.clock())


@router.get("/{quote_id}", summary="One quote, line by line", responses=_ERRORS | _NOT_FOUND)
async def read_quote(
    quote_id: UUID, principal: PrincipalDep, services: ServicesDep, response: Response
) -> QuoteResponse:
    quote = await get_quote(principal, quote_id, unit_of_work=services.unit_of_work)
    return _respond(response, quote, principal, services.clock())
