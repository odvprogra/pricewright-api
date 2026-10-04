"""Pricing rules: everyone reads them (``pricing:read``); managers and admins maintain them
(``pricing:manage``). See ADR-0018 for what a rule is."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from http import HTTPStatus
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pricewright.api.concurrency import etag, expected_version
from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.api.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, encode_cursor
from pricewright.api.times import UtcDatetime
from pricewright.application.ports import PricingRuleQuery, PricingRuleSort
from pricewright.application.pricing_rules import (
    NewPricingRule,
    PricingRuleChanges,
    change_pricing_rule,
    create_pricing_rule,
    get_pricing_rule,
    list_pricing_rules,
)
from pricewright.domain.customers import CustomerTier
from pricewright.domain.pricing_rules import (
    MAX_BRACKETS,
    MAX_RULE_NAME_LENGTH,
    RATE_DECIMAL_PLACES,
    Bracket,
    PricingRule,
    RuleKind,
)
from pricewright.domain.quantities import QUANTITY_DECIMAL_PLACES
from pricewright.domain.updates import KEEP

router = APIRouter(prefix="/api/v1/pricing-rules", tags=["pricing"])

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"description": "Missing or invalid credentials"},
    403: {
        "description": "Reading needs `pricing:read`; changes need `pricing:manage` "
        "(managers and admins)"
    },
}
_NOT_FOUND: dict[int | str, dict[str, object]] = {
    404: {"description": "No such pricing rule in this tenant (ADR-0009)"}
}
_INVALID: dict[int | str, dict[str, object]] = {
    422: {
        "description": "Invalid fields, a rule inconsistent with its kind, or an unknown product "
        "or category"
    }
}
_RATE = "A fraction with up to 4 places: 0.125 is 12.5%."
_RATE_PLACES = Decimal(1).scaleb(-RATE_DECIMAL_PLACES)
_QUANTITY_PLACES = Decimal(1).scaleb(-QUANTITY_DECIMAL_PLACES)


class BracketJson(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    min_quantity: Decimal = Field(
        description="From this many units on, up to 3 decimal places.", examples=["100"]
    )
    rate: Decimal = Field(description=f"Off the whole line. {_RATE}", examples=["0.1"])

    def to_bracket(self) -> Bracket:
        return Bracket(self.min_quantity, self.rate)


class PricingRuleResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    # Open-ended (ADR-0015): kinds may be added.
    kind: str = Field(
        description="The stage it feeds, or `margin_floor`. New kinds may appear; handle unknown "
        "ones.",
        examples=list(RuleKind),
    )
    name: str
    product_id: UUID | None = Field(description="Set when the rule is about one product.")
    category_id: UUID | None = Field(description="Set when the rule is about one category.")
    customer_tier: CustomerTier | None = Field(description="Set on customer tier discounts.")
    rate: Decimal | None = Field(
        description=f"The discount, or a margin floor's least margin. {_RATE} Null for volume "
        "tiers, whose rates are in their brackets."
    )
    brackets: list[BracketJson] = Field(description="Volume tiers only, by minimum quantity.")
    valid_from: datetime = Field(description="Inclusive.")
    valid_to: datetime | None = Field(description="Exclusive; null when open-ended.")
    is_active: bool
    version: int = Field(description="Also sent as the ETag; send it back in If-Match to update.")

    @classmethod
    def of(cls, rule: PricingRule) -> PricingRuleResponse:
        return cls(
            id=rule.id,
            kind=rule.kind.value,
            name=rule.name,
            product_id=rule.product_id,
            category_id=rule.category_id,
            customer_tier=rule.customer_tier,
            rate=None if rule.rate is None else rule.rate.quantize(_RATE_PLACES),
            brackets=[
                BracketJson(
                    min_quantity=bracket.min_quantity.quantize(_QUANTITY_PLACES),
                    rate=bracket.rate.quantize(_RATE_PLACES),
                )
                for bracket in rule.brackets
            ],
            valid_from=rule.valid_from,
            valid_to=rule.valid_to,
            is_active=rule.is_active,
            version=rule.version,
        )


class CreatePricingRuleRequest(BaseModel):
    """Volume tiers take `brackets`; every other kind takes a `rate`. A rule is about one product,
    one category or, with neither, every product."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RuleKind
    name: str = Field(max_length=MAX_RULE_NAME_LENGTH, examples=["Fasteners: 100 boxes or more"])
    product_id: UUID | None = None
    category_id: UUID | None = None
    customer_tier: CustomerTier | None = Field(
        default=None, description="Required on customer tier discounts, refused on other kinds."
    )
    rate: Decimal | None = Field(default=None, description=_RATE, examples=["0.05"])
    brackets: list[BracketJson] = Field(default_factory=list, max_length=MAX_BRACKETS)
    valid_from: UtcDatetime | None = Field(default=None, description="Inclusive; defaults to now.")
    valid_to: UtcDatetime | None = Field(
        default=None, description="Exclusive; required on promotions."
    )

    def new(self) -> NewPricingRule:
        return NewPricingRule(
            kind=self.kind,
            name=self.name,
            rate=self.rate,
            brackets=tuple(bracket.to_bracket() for bracket in self.brackets),
            product_id=self.product_id,
            category_id=self.category_id,
            customer_tier=self.customer_tier,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
        )


class PricingRulePatch(BaseModel):
    """Only the fields sent change; `valid_to: null` removes the end. Kind, scope and tier cannot:
    deactivate the rule and create another."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = Field(default=None, max_length=MAX_RULE_NAME_LENGTH)
    rate: Decimal | None = None
    brackets: list[BracketJson] | None = Field(default=None, max_length=MAX_BRACKETS)
    valid_from: UtcDatetime | None = None
    valid_to: UtcDatetime | None = None
    is_active: bool | None = Field(default=None, description="False deactivates the rule.")

    @model_validator(mode="after")
    def _changes_something(self) -> Self:
        if self.model_fields_set == set():
            raise ValueError("send at least one field to change")
        return self

    def changes(self) -> PricingRuleChanges:
        return PricingRuleChanges(
            name=self.name,
            rate=self.rate,
            brackets=None
            if self.brackets is None
            else tuple(bracket.to_bracket() for bracket in self.brackets),
            valid_from=self.valid_from,
            valid_to=self.valid_to if "valid_to" in self.model_fields_set else KEEP,
            is_active=self.is_active,
        )


class PricingRulePage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[PricingRuleResponse]
    next_cursor: str | None


class PricingRuleOrder(StrEnum):
    NAME = "name"
    NAME_DESCENDING = "-name"
    OLDEST = "created_at"
    NEWEST = "-created_at"


@router.get("", summary="List the pricing rules", responses=_ERRORS)
async def read_pricing_rules(
    principal: PrincipalDep,
    services: ServicesDep,
    kind: RuleKind | None = None,
    product_id: UUID | None = None,
    category_id: UUID | None = None,
    customer_tier: CustomerTier | None = None,
    active: Annotated[bool | None, Query(description="Omit it to list inactive ones too.")] = None,
    sort: Annotated[
        PricingRuleOrder, Query(description="`-` sorts descending.")
    ] = PricingRuleOrder.NAME,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> PricingRulePage:
    query = PricingRuleQuery(
        kind=kind,
        product_id=product_id,
        category_id=category_id,
        customer_tier=customer_tier,
        active=active,
        sort=PricingRuleSort(sort.removeprefix("-")),
        descending=sort.startswith("-"),
    )
    fingerprint = {
        "kind": kind,
        "product_id": product_id,
        "category_id": category_id,
        "customer_tier": customer_tier,
        "active": active,
        "sort": sort,
    }
    page = await list_pricing_rules(
        principal,
        query,
        after=decode_cursor(cursor, fingerprint),
        limit=limit,
        unit_of_work=services.unit_of_work,
    )
    return PricingRulePage(
        items=[PricingRuleResponse.of(rule) for rule in page.items],
        next_cursor=encode_cursor(page.next_after, fingerprint),
    )


@router.get("/{rule_id}", summary="One pricing rule", responses=_ERRORS | _NOT_FOUND)
async def read_pricing_rule(
    rule_id: UUID, principal: PrincipalDep, services: ServicesDep, response: Response
) -> PricingRuleResponse:
    rule = await get_pricing_rule(principal, rule_id, unit_of_work=services.unit_of_work)
    response.headers["ETag"] = etag(rule.version)
    return PricingRuleResponse.of(rule)


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    summary="Add a pricing rule",
    responses=_ERRORS | _INVALID,
)
async def add_pricing_rule(
    body: CreatePricingRuleRequest,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
) -> PricingRuleResponse:
    rule = await create_pricing_rule(
        principal, body.new(), unit_of_work=services.unit_of_work, clock=services.clock
    )
    response.headers["Location"] = f"{router.prefix}/{rule.id}"
    response.headers["ETag"] = etag(rule.version)
    return PricingRuleResponse.of(rule)


@router.patch(
    "/{rule_id}",
    summary="Edit, end-date or deactivate a pricing rule",
    responses=_ERRORS
    | _NOT_FOUND
    | _INVALID
    | {
        412: {"description": "If-Match is stale: someone else changed the rule; reload it"},
        428: {"description": "If-Match is required"},
    },
)
async def update_pricing_rule(
    rule_id: UUID,
    body: PricingRulePatch,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> PricingRuleResponse:
    rule = await change_pricing_rule(
        principal,
        rule_id,
        body.changes(),
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    response.headers["ETag"] = etag(rule.version)
    return PricingRuleResponse.of(rule)
