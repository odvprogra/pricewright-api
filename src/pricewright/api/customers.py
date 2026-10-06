"""Customers: every role reads and manages them (``customers:read`` / ``customers:manage``)."""

from enum import StrEnum
from http import HTTPStatus
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pricewright.api.concurrency import etag, expected_version
from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.api.idempotency import (
    IdempotencyKey,
    idempotency_errors,
    idempotent_request,
    mark_replayed,
)
from pricewright.api.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, encode_cursor
from pricewright.application.customers import (
    CustomerChanges,
    NewCustomer,
    change_customer,
    create_customer,
    get_customer,
    list_customers,
)
from pricewright.application.ports import CustomerQuery, CustomerSort
from pricewright.domain.customers import (
    DEFAULT_PAYMENT_TERMS_DAYS,
    MAX_ACCOUNT_NUMBER_LENGTH,
    MAX_CUSTOMER_NAME_LENGTH,
    MAX_PAYMENT_TERMS_DAYS,
    Customer,
    CustomerTier,
    compact_tax_id,
)
from pricewright.domain.updates import KEEP

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])
MAX_SEARCH_LENGTH = 100
_TAX_ID_INPUT_LENGTH = 50  # before compaction: separators are allowed

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"description": "Missing or invalid credentials"},
    403: {"description": "Needs `customers:read`, or `customers:manage` for changes"},
}
_NOT_FOUND: dict[int | str, dict[str, object]] = {
    404: {"description": "No such customer in this tenant (ADR-0009)"}
}
_TAX_ID = "Spaces, dots, slashes and hyphens are ignored: `de 123.456-789` is `DE123456789`."
_TERMS = "Net days to pay: 0 is due on receipt."


class CustomerResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    account_number: str
    name: str
    tax_id: str | None = Field(description="Compact: upper case, no separators.")
    tier: CustomerTier
    payment_terms_days: int = Field(description=_TERMS)
    is_active: bool = Field(description="False once archived: kept for history.")
    version: int = Field(description="Also sent as the ETag; send it back in If-Match to update.")

    @classmethod
    def of(cls, customer: Customer) -> CustomerResponse:
        return cls(
            id=customer.id,
            account_number=customer.account_number,
            name=customer.name,
            tax_id=customer.tax_id,
            tier=customer.tier,
            payment_terms_days=customer.payment_terms_days,
            is_active=customer.is_active,
            version=customer.version,
        )


PaymentTerms = Annotated[int, Field(ge=0, le=MAX_PAYMENT_TERMS_DAYS, description=_TERMS)]


class CreateCustomerRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    account_number: str = Field(
        max_length=MAX_ACCOUNT_NUMBER_LENGTH,
        description="Unique in the tenant, ignoring case; never changes.",
        examples=["C-1001"],
    )
    name: str = Field(max_length=MAX_CUSTOMER_NAME_LENGTH, examples=["Acme Industrial"])
    tier: CustomerTier = CustomerTier.STANDARD
    payment_terms_days: PaymentTerms = DEFAULT_PAYMENT_TERMS_DAYS
    tax_id: str | None = Field(default=None, max_length=_TAX_ID_INPUT_LENGTH, description=_TAX_ID)


class CustomerPatch(BaseModel):
    """Only the fields sent change; `tax_id: null` removes it. The account number cannot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = Field(default=None, max_length=MAX_CUSTOMER_NAME_LENGTH)
    tier: CustomerTier | None = None
    payment_terms_days: PaymentTerms | None = None
    tax_id: str | None = Field(default=None, max_length=_TAX_ID_INPUT_LENGTH, description=_TAX_ID)
    is_active: bool | None = Field(default=None, description="False archives the customer.")

    @model_validator(mode="after")
    def _changes_something(self) -> Self:
        if self.model_fields_set == set():
            raise ValueError("send at least one field to change")
        return self

    def changes(self) -> CustomerChanges:
        return CustomerChanges(
            name=self.name,
            tier=self.tier,
            payment_terms_days=self.payment_terms_days,
            tax_id=self.tax_id if "tax_id" in self.model_fields_set else KEEP,
            is_active=self.is_active,
        )


class CustomerPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[CustomerResponse]
    next_cursor: str | None


class CustomerOrder(StrEnum):
    NAME = "name"
    NAME_DESCENDING = "-name"
    ACCOUNT_NUMBER = "account_number"
    ACCOUNT_NUMBER_DESCENDING = "-account_number"
    OLDEST = "created_at"
    NEWEST = "-created_at"


@router.get("", summary="Search and list customers", responses=_ERRORS)
async def read_customers(
    principal: PrincipalDep,
    services: ServicesDep,
    q: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=MAX_SEARCH_LENGTH,
            description="Text contained in the account number or the name, ignoring case.",
        ),
    ] = None,
    tax_id: Annotated[
        str | None, Query(max_length=_TAX_ID_INPUT_LENGTH, description=f"Exact. {_TAX_ID}")
    ] = None,
    tier: CustomerTier | None = None,
    active: Annotated[bool | None, Query(description="Omit it to list archived ones too.")] = None,
    sort: Annotated[CustomerOrder, Query(description="`-` sorts descending.")] = CustomerOrder.NAME,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> CustomerPage:
    query = CustomerQuery(
        text=q,
        tax_id=None if tax_id is None else compact_tax_id(tax_id),
        tier=tier,
        active=active,
        sort=CustomerSort(sort.removeprefix("-")),
        descending=sort.startswith("-"),
    )
    fingerprint = {
        "q": q,
        "tax_id": query.tax_id,
        "tier": tier,
        "active": active,
        "sort": sort,
    }
    page = await list_customers(
        principal,
        query,
        after=decode_cursor(cursor, fingerprint),
        limit=limit,
        unit_of_work=services.unit_of_work,
    )
    return CustomerPage(
        items=[CustomerResponse.of(customer) for customer in page.items],
        next_cursor=encode_cursor(page.next_after, fingerprint),
    )


@router.get("/{customer_id}", summary="One customer", responses=_ERRORS | _NOT_FOUND)
async def read_customer(
    customer_id: UUID, principal: PrincipalDep, services: ServicesDep, response: Response
) -> CustomerResponse:
    customer = await get_customer(principal, customer_id, unit_of_work=services.unit_of_work)
    response.headers["ETag"] = etag(customer.version)
    return CustomerResponse.of(customer)


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    summary="Add a customer",
    responses=idempotency_errors(
        _ERRORS | {409: {"description": "The account number exists, ignoring case"}}
    ),
)
async def add_customer(
    body: CreateCustomerRequest,
    principal: PrincipalDep,
    services: ServicesDep,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> CustomerResponse:
    created = await create_customer(
        principal,
        NewCustomer(
            account_number=body.account_number,
            name=body.name,
            tier=body.tier,
            payment_terms_days=body.payment_terms_days,
            tax_id=body.tax_id,
        ),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
        idempotency=idempotent_request(idempotency_key, request, body),
    )
    customer = created.value
    mark_replayed(response, created.replayed)
    response.headers["Location"] = f"{router.prefix}/{customer.id}"
    response.headers["ETag"] = etag(customer.version)
    return CustomerResponse.of(customer)


@router.patch(
    "/{customer_id}",
    summary="Edit, archive or restore a customer",
    responses=_ERRORS
    | _NOT_FOUND
    | {
        412: {"description": "If-Match is stale: someone else changed the customer; reload it"},
        428: {"description": "If-Match is required"},
    },
)
async def update_customer(
    customer_id: UUID,
    body: CustomerPatch,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> CustomerResponse:
    customer = await change_customer(
        principal,
        customer_id,
        body.changes(),
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    response.headers["ETag"] = etag(customer.version)
    return CustomerResponse.of(customer)
