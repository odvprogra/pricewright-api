"""Orders (``orders:read`` / ``orders:manage``): accepted quotes converted into what the customer
committed to, their prices copied unchanged (ADR-0023); costs and margins only reach people
(ADR-0017)."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from pricewright.api.concurrency import etag, expected_version
from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.api.idempotency import IdempotencyKey, idempotent_request, mark_replayed
from pricewright.api.money import MoneyJson
from pricewright.api.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, encode_cursor
from pricewright.api.pricing import ratio
from pricewright.api.quotes import ActorJson, IfMatch, PricedLineJson
from pricewright.application.orders import cancel_order, get_order, list_orders
from pricewright.application.ports import OrderQuery, OrderSummary
from pricewright.application.quotes import convert_quote
from pricewright.domain.auth import Principal
from pricewright.domain.orders import (
    MAX_CANCEL_REASON_LENGTH,
    MAX_CUSTOMER_REFERENCE_LENGTH,
    Order,
    OrderLine,
    OrderStatus,
)

router = APIRouter(prefix="/api/v1", tags=["orders"])

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"description": "Missing or invalid credentials"},
    403: {"description": "Needs `orders:read`"},
}
_NOT_FOUND: dict[int | str, dict[str, object]] = {
    404: {"description": "No such order in this tenant (ADR-0009)"}
}


class OrderLineJson(PricedLineJson):
    """An accepted quote line, copied unchanged."""

    @classmethod
    def of(cls, line: OrderLine, caller: Principal) -> OrderLineJson:
        return cls(**cls.fields_of(line, caller))


class OrderCustomerJson(BaseModel):
    """The customer as the order was placed; later changes to the customer do not reach it."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    account_number: str
    name: str
    tax_id: str | None
    payment_terms_days: int = Field(description="Net days, from the customer at conversion.")


class OrderResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    number: str = Field(description="The tenant's order prefix, the year and a counter.")
    quote_id: UUID
    quote_number: str = Field(
        description="The accepted revision's number.", examples=["NF-2026-000123-R2"]
    )
    customer: OrderCustomerJson
    customer_reference: str | None = Field(
        description="The customer's own reference, such as its purchase order number."
    )
    # Open-ended (ADR-0015): fulfilment and invoicing would add statuses.
    status: str = Field(
        description="New statuses may appear; handle unknown ones.", examples=list(OrderStatus)
    )
    lines: list[OrderLineJson]
    list_subtotal: MoneyJson
    net_subtotal: MoneyJson
    tax_rate: Decimal = Field(description="The rate the quote was priced with (decision D-09).")
    tax: MoneyJson
    total: MoneyJson
    priced_at: datetime = Field(description="When the quote's prices were set, at submission.")
    created_by: ActorJson
    created_at: datetime
    status_changed_at: datetime
    cancel_reason: str | None
    version: int = Field(description="Also sent as the ETag; send it back in If-Match to change.")

    @classmethod
    def of(cls, order: Order, caller: Principal) -> OrderResponse:
        totals, customer = order.totals, order.customer
        return cls(
            id=order.id,
            number=order.number,
            quote_id=order.quote_id,
            quote_number=order.quote_number,
            customer=OrderCustomerJson(
                id=customer.id,
                account_number=customer.account_number,
                name=customer.name,
                tax_id=customer.tax_id,
                payment_terms_days=customer.payment_terms_days,
            ),
            customer_reference=order.customer_reference,
            status=order.status.value,
            lines=[OrderLineJson.of(line, caller) for line in order.lines],
            list_subtotal=MoneyJson.of(totals.list_subtotal),
            net_subtotal=MoneyJson.of(totals.net_subtotal),
            tax_rate=ratio(totals.tax_rate),
            tax=MoneyJson.of(totals.tax),
            total=MoneyJson.of(totals.total),
            priced_at=totals.priced_at,
            created_by=ActorJson.of(order.created_by),
            created_at=order.created_at,
            status_changed_at=order.status_changed_at or order.created_at,
            cancel_reason=order.cancel_reason,
            version=order.version,
        )


def _respond(response: Response, order: Order, caller: Principal) -> OrderResponse:
    response.headers["ETag"] = etag(order.version)
    return OrderResponse.of(order, caller)


class ConvertRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    customer_reference: str | None = Field(
        default=None,
        max_length=MAX_CUSTOMER_REFERENCE_LENGTH,
        description="The customer's own reference, such as its purchase order number.",
        examples=["PO-4500123"],
    )


@router.post(
    "/quotes/{quote_id}/convert",
    status_code=HTTPStatus.CREATED,
    summary="Convert an accepted quote into its order (people only)",
    description="Copies the accepted quote's lines, prices and tax unchanged into a new order, "
    "and the quote becomes `converted`. `If-Match` carries the quote's ETag. A retry with the same "
    "`Idempotency-Key` gets the same order back, even with an old ETag.",
    responses={
        401: {"description": "Missing or invalid credentials"},
        403: {"description": "Needs `orders:manage`; never integrations"},
        404: {"description": "No such quote in this tenant (ADR-0009)"},
        409: {
            "description": "The quote is not accepted (`invalid_transition`), or a request with "
            "this Idempotency-Key is still running (`idempotency_key_in_use`)"
        },
        412: {"description": "If-Match is stale: someone else changed the quote; reload it"},
        422: {
            "description": "An archived customer, an invalid reference, or an Idempotency-Key "
            "that is invalid or was used for a different request"
        },
        428: {"description": "If-Match is required"},
    },
)
async def convert(
    quote_id: UUID,
    body: ConvertRequest,
    principal: PrincipalDep,
    services: ServicesDep,
    request: Request,
    response: Response,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> OrderResponse:
    created = await convert_quote(
        principal,
        quote_id,
        body.customer_reference,
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
        idempotency=idempotent_request(idempotency_key, request, body),
    )
    order = created.value
    response.headers["Location"] = f"{router.prefix}/orders/{order.id}"
    mark_replayed(response, created.replayed)
    return _respond(response, order, principal)


@router.get("/orders/{order_id}", summary="One order, line by line", responses=_ERRORS | _NOT_FOUND)
async def read_order(
    order_id: UUID, principal: PrincipalDep, services: ServicesDep, response: Response
) -> OrderResponse:
    order = await get_order(principal, order_id, unit_of_work=services.unit_of_work)
    return _respond(response, order, principal)


class OrderSummaryJson(BaseModel):
    """An order as lists show it: no lines."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    number: str
    quote_id: UUID
    quote_number: str = Field(examples=["NF-2026-000123-R2"])
    customer_id: UUID
    customer_name: str = Field(description="As the order was placed.")
    customer_reference: str | None
    # Open-ended (ADR-0015): fulfilment and invoicing would add statuses.
    status: str = Field(
        description="New statuses may appear; handle unknown ones.", examples=list(OrderStatus)
    )
    total: MoneyJson
    created_by: ActorJson
    created_at: datetime
    status_changed_at: datetime
    version: int

    @classmethod
    def of(cls, order: OrderSummary) -> OrderSummaryJson:
        return cls(
            id=order.id,
            number=order.number,
            quote_id=order.quote_id,
            quote_number=order.quote_number,
            customer_id=order.customer_id,
            customer_name=order.customer_name,
            customer_reference=order.customer_reference,
            status=order.status.value,
            total=MoneyJson.of(order.total),
            created_by=ActorJson.of(order.created_by),
            created_at=order.created_at,
            status_changed_at=order.status_changed_at,
            version=order.version,
        )


class OrderPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[OrderSummaryJson]
    next_cursor: str | None


class OrderOrder(StrEnum):
    NEWEST = "-created_at"
    OLDEST = "created_at"


@router.get("/orders", summary="List orders, newest first by default", responses=_ERRORS)
async def read_orders(
    principal: PrincipalDep,
    services: ServicesDep,
    status: OrderStatus | None = None,
    customer_id: UUID | None = None,
    number: Annotated[
        str | None,
        Query(min_length=1, max_length=24, description="The exact order number."),
    ] = None,
    created_by: Annotated[UUID | None, Query(description="The person who converted it.")] = None,
    sort: Annotated[OrderOrder, Query(description="`-` sorts descending.")] = OrderOrder.NEWEST,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> OrderPage:
    query = OrderQuery(
        status=status,
        customer_id=customer_id,
        number=number,
        created_by=created_by,
        descending=sort is OrderOrder.NEWEST,
    )
    fingerprint = {
        "status": status,
        "customer_id": None if customer_id is None else str(customer_id),
        "number": number,
        "created_by": None if created_by is None else str(created_by),
        "sort": sort,
    }
    page = await list_orders(
        principal,
        query,
        after=decode_cursor(cursor, fingerprint),
        limit=limit,
        unit_of_work=services.unit_of_work,
    )
    return OrderPage(
        items=[OrderSummaryJson.of(order) for order in page.items],
        next_cursor=encode_cursor(page.next_after, fingerprint),
    )


class CancelOrderRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(
        min_length=1,
        max_length=MAX_CANCEL_REASON_LENGTH,
        examples=["Entered for the wrong customer"],
    )


@router.post(
    "/orders/{order_id}/cancel",
    summary="Cancel an open order, with a reason (people only)",
    description="The order stays, as every document does, and its quote stays `converted`.",
    responses=_ERRORS
    | _NOT_FOUND
    | {
        403: {"description": "Needs `orders:manage`; never integrations"},
        409: {"description": "The order is not open (`invalid_transition`)"},
        412: {"description": "If-Match is stale: someone else changed the order; reload it"},
        422: {"description": "A reason is required (`invalid_order`, `validation_error`)"},
        428: {"description": "If-Match is required"},
    },
)
async def cancel(
    order_id: UUID,
    body: CancelOrderRequest,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: IfMatch = None,
) -> OrderResponse:
    order = await cancel_order(
        principal,
        order_id,
        body.reason,
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    return _respond(response, order, principal)
