"""The product catalog: everyone reads it; admins add, edit and archive products."""

from enum import StrEnum
from http import HTTPStatus
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pricewright.api.concurrency import etag, expected_version
from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.api.money import MoneyJson
from pricewright.api.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, encode_cursor
from pricewright.application.catalog import (
    NewProduct,
    ProductChanges,
    change_product,
    create_product,
    get_product,
    list_products,
)
from pricewright.application.ports import ProductQuery, ProductSort
from pricewright.domain.catalog import (
    MAX_PRODUCT_NAME_LENGTH,
    MAX_SKU_LENGTH,
    Product,
    UnitOfMeasure,
)
from pricewright.domain.updates import KEEP

router = APIRouter(prefix="/api/v1/products", tags=["catalog"])
MAX_SEARCH_LENGTH = 100

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"description": "Missing or invalid credentials"},
    403: {"description": "Reading needs `catalog:read`; changes need `catalog:manage` (admins)"},
}
_NOT_FOUND: dict[int | str, dict[str, object]] = {
    404: {"description": "No such product in this tenant (ADR-0009)"}
}


class ProductResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    sku: str
    name: str
    category_id: UUID | None
    # Open-ended (ADR-0015): units may be added.
    unit: str = Field(
        description="UN/ECE Recommendation 20 code. New units may appear; handle unknown ones.",
        examples=list(UnitOfMeasure),
    )
    list_price: MoneyJson
    unit_cost: MoneyJson
    is_active: bool = Field(description="False once archived: kept for history, not for sale.")
    version: int = Field(description="Also sent as the ETag; send it back in If-Match to update.")

    @classmethod
    def of(cls, product: Product) -> ProductResponse:
        return cls(
            id=product.id,
            sku=product.sku,
            name=product.name,
            category_id=product.category_id,
            unit=product.unit.value,
            list_price=MoneyJson.of(product.list_price),
            unit_cost=MoneyJson.of(product.unit_cost),
            is_active=product.is_active,
            version=product.version,
        )


class CreateProductRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(
        max_length=MAX_SKU_LENGTH,
        description="Unique in the tenant, ignoring case; never changes.",
        examples=["FAS-M6-100"],
    )
    name: str = Field(max_length=MAX_PRODUCT_NAME_LENGTH, examples=["Hex bolt M6 x 100"])
    category_id: UUID | None = None
    unit: UnitOfMeasure
    list_price: MoneyJson
    unit_cost: MoneyJson


class ProductPatch(BaseModel):
    """Only the fields sent change; `category_id: null` removes the category. The SKU cannot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = Field(default=None, max_length=MAX_PRODUCT_NAME_LENGTH)
    category_id: UUID | None = None
    unit: UnitOfMeasure | None = None
    list_price: MoneyJson | None = None
    unit_cost: MoneyJson | None = None
    is_active: bool | None = Field(default=None, description="False archives the product.")

    @model_validator(mode="after")
    def _changes_something(self) -> Self:
        if self.model_fields_set == set():
            raise ValueError("send at least one field to change")
        return self

    def changes(self) -> ProductChanges:
        return ProductChanges(
            name=self.name,
            unit=self.unit,
            list_price=None if self.list_price is None else self.list_price.to_money(),
            unit_cost=None if self.unit_cost is None else self.unit_cost.to_money(),
            category_id=self.category_id if "category_id" in self.model_fields_set else KEEP,
            is_active=self.is_active,
        )


class ProductPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[ProductResponse]
    next_cursor: str | None


class ProductOrder(StrEnum):
    NAME = "name"
    NAME_DESCENDING = "-name"
    SKU = "sku"
    SKU_DESCENDING = "-sku"
    OLDEST = "created_at"
    NEWEST = "-created_at"


@router.get("", summary="Search and list the catalog", responses=_ERRORS)
async def read_products(
    principal: PrincipalDep,
    services: ServicesDep,
    q: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=MAX_SEARCH_LENGTH,
            description="Text contained in the SKU or the name, ignoring case.",
        ),
    ] = None,
    sku: Annotated[str | None, Query(description="The exact SKU, ignoring case.")] = None,
    category_id: UUID | None = None,
    active: Annotated[bool | None, Query(description="Omit it to list archived ones too.")] = None,
    sort: Annotated[ProductOrder, Query(description="`-` sorts descending.")] = ProductOrder.NAME,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> ProductPage:
    query = ProductQuery(
        text=q,
        sku=sku,
        category_id=category_id,
        active=active,
        sort=ProductSort(sort.removeprefix("-")),
        descending=sort.startswith("-"),
    )
    fingerprint = {"q": q, "sku": sku, "category_id": category_id, "active": active, "sort": sort}
    page = await list_products(
        principal,
        query,
        after=decode_cursor(cursor, fingerprint),
        limit=limit,
        unit_of_work=services.unit_of_work,
    )
    return ProductPage(
        items=[ProductResponse.of(product) for product in page.items],
        next_cursor=encode_cursor(page.next_after, fingerprint),
    )


@router.get("/{product_id}", summary="One product", responses=_ERRORS | _NOT_FOUND)
async def read_product(
    product_id: UUID, principal: PrincipalDep, services: ServicesDep, response: Response
) -> ProductResponse:
    product = await get_product(principal, product_id, unit_of_work=services.unit_of_work)
    response.headers["ETag"] = etag(product.version)
    return ProductResponse.of(product)


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    summary="Add a product to the catalog",
    responses=_ERRORS
    | {
        409: {"description": "The SKU is already in the catalog, ignoring case"},
        422: {
            "description": "Invalid fields, an unknown category, or prices not in the "
            "tenant's currency"
        },
    },
)
async def add_product(
    body: CreateProductRequest, principal: PrincipalDep, services: ServicesDep, response: Response
) -> ProductResponse:
    product = await create_product(
        principal,
        NewProduct(
            sku=body.sku,
            name=body.name,
            unit=body.unit,
            list_price=body.list_price.to_money(),
            unit_cost=body.unit_cost.to_money(),
            category_id=body.category_id,
        ),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    response.headers["Location"] = f"{router.prefix}/{product.id}"
    response.headers["ETag"] = etag(product.version)
    return ProductResponse.of(product)


@router.patch(
    "/{product_id}",
    summary="Edit, archive or restore a product",
    responses=_ERRORS
    | _NOT_FOUND
    | {
        412: {"description": "If-Match is stale: someone else changed the product; reload it"},
        428: {"description": "If-Match is required"},
    },
)
async def update_product(
    product_id: UUID,
    body: ProductPatch,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ProductResponse:
    product = await change_product(
        principal,
        product_id,
        body.changes(),
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    response.headers["ETag"] = etag(product.version)
    return ProductResponse.of(product)
