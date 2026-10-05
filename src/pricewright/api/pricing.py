"""Price previews (``pricing:read``): the pricing engine's result for some lines, explained step by
step (ADR-0004). Nothing is saved; costs and margins only reach people (ADR-0017)."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.api.money import MoneyJson
from pricewright.api.times import UtcDatetime
from pricewright.application.pricing import MAX_PREVIEW_LINES, PreviewLine, preview_prices
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.catalog import Product
from pricewright.domain.pricing import (
    RATIO_DECIMAL_PLACES,
    Adjustment,
    ApprovalReason,
    PricedLine,
    Stage,
)
from pricewright.domain.quantities import QUANTITY_DECIMAL_PLACES

router = APIRouter(prefix="/api/v1/pricing", tags=["pricing"])

_FOUR_PLACES = Decimal(1).scaleb(-RATIO_DECIMAL_PLACES)
_QUANTITY_PLACES = Decimal(1).scaleb(-QUANTITY_DECIMAL_PLACES)


def ratio(value: Decimal) -> Decimal:
    return value.quantize(_FOUR_PLACES)


class PreviewLineJson(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    product_id: UUID
    quantity: Decimal = Field(description="More than 0, up to 3 decimal places.", examples=["10"])


class PricePreviewRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    customer_id: UUID
    lines: list[PreviewLineJson] = Field(min_length=1, max_length=MAX_PREVIEW_LINES)
    priced_at: UtcDatetime | None = Field(
        default=None, description="Price with the rules effective then; defaults to now."
    )


class StepJson(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Open-ended (ADR-0015): stages may be added.
    stage: str = Field(
        description="New stages may appear; handle unknown ones.", examples=list(Stage)
    )
    rule_id: UUID | None = Field(description="Null for a manual override.")
    label: str = Field(description="The rule's name, or the override's reason.")
    rate: Decimal | None = Field(description="The rate off; null when an override set the price.")
    amount: MoneyJson = Field(description="Per unit; negative when it takes something off.")
    unit_price: MoneyJson = Field(description="The unit price after this step.")

    @classmethod
    def of(cls, step: Adjustment) -> StepJson:
        return cls(
            stage=step.stage.value,
            rule_id=step.rule_id,
            label=step.label,
            rate=None if step.rate is None else ratio(step.rate),
            amount=MoneyJson.of(step.amount),
            unit_price=MoneyJson.of(step.unit_price),
        )


class MarginJson(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount: MoneyJson = Field(description="The line's net total less its cost.")
    rate: Decimal | None = Field(
        description="Gross margin on the selling price; null for a line given away."
    )


class MarginFloorJson(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: UUID
    label: str
    rate: Decimal = Field(description="The least margin the line may keep without approval.")


class PricedLineJson(BaseModel):
    model_config = ConfigDict(frozen=True)

    product_id: UUID
    sku: str
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

    @classmethod
    def of(cls, line: PricedLine, product: Product, caller: Principal) -> PricedLineJson:
        floor = line.margin_floor
        margin = line.margin_rate
        return cls(
            product_id=line.product_id,
            sku=product.sku,
            quantity=line.quantity.quantize(_QUANTITY_PLACES),
            list_unit_price=MoneyJson.of(line.breakdown.list_unit_price),
            steps=[StepJson.of(step) for step in line.breakdown.steps],
            net_unit_price=MoneyJson.of(line.breakdown.net_unit_price),
            list_total=MoneyJson.of(line.list_total),
            net_total=MoneyJson.of(line.net_total),
            margin=MarginJson(
                amount=MoneyJson.of(line.margin),
                rate=None if margin is None else ratio(margin),
            )
            if caller.holds(Permission.COSTS_READ)
            else None,
            margin_floor=None
            if floor is None
            else MarginFloorJson(rule_id=floor.rule_id, label=floor.label, rate=ratio(floor.rate)),
            below_margin_floor=line.below_margin_floor,
        )


class PricePreviewResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    priced_at: datetime
    customer_id: UUID
    lines: list[PricedLineJson]
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


@router.post(
    "/preview",
    summary="Price lines for a customer, explained step by step, without saving anything",
    responses={
        401: {"description": "Missing or invalid credentials"},
        403: {"description": "Needs `pricing:read`"},
        422: {
            "description": "Invalid lines, or an unknown or archived customer or product of this "
            "tenant"
        },
    },
)
async def preview(
    body: PricePreviewRequest, principal: PrincipalDep, services: ServicesDep
) -> PricePreviewResponse:
    result = await preview_prices(
        principal,
        body.customer_id,
        [PreviewLine(line.product_id, line.quantity) for line in body.lines],
        priced_at=body.priced_at,
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    quote = result.quote
    return PricePreviewResponse(
        priced_at=result.priced_at,
        customer_id=body.customer_id,
        lines=[
            PricedLineJson.of(line, result.products[line.product_id], principal)
            for line in quote.lines
        ],
        list_subtotal=MoneyJson.of(quote.list_subtotal),
        net_subtotal=MoneyJson.of(quote.net_subtotal),
        tax_rate=ratio(quote.tax_rate),
        tax=MoneyJson.of(quote.tax),
        total=MoneyJson.of(quote.total),
        discount=quote.discount,
        approval_threshold=ratio(quote.approval_threshold),
        requires_approval=quote.requires_approval,
        approval_reasons=[reason.value for reason in quote.approval_reasons],
    )
