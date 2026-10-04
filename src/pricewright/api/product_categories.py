"""Product categories: everyone reads them; admins add and rename them (``catalog:manage``)."""

from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Response
from pydantic import BaseModel, ConfigDict, Field

from pricewright.api.concurrency import etag, expected_version
from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.api.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, encode_cursor
from pricewright.application.catalog import (
    create_category,
    get_category,
    list_categories,
    rename_category,
)
from pricewright.domain.catalog import MAX_CATEGORY_NAME_LENGTH, ProductCategory

router = APIRouter(prefix="/api/v1/product-categories", tags=["catalog"])

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"description": "Missing or invalid credentials"},
    403: {"description": "Reading needs `catalog:read`; changes need `catalog:manage` (admins)"},
}
_NOT_FOUND: dict[int | str, dict[str, object]] = {
    404: {"description": "No such category in this tenant (ADR-0009)"}
}
_NAME_TAKEN: dict[int | str, dict[str, object]] = {
    409: {"description": "Another category has that name, ignoring case"}
}


class CategoryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    version: int = Field(description="Also sent as the ETag; send it back in If-Match to update.")

    @classmethod
    def of(cls, category: ProductCategory) -> CategoryResponse:
        return cls(id=category.id, name=category.name, version=category.version)


class CategoryRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(max_length=MAX_CATEGORY_NAME_LENGTH, examples=["Fasteners"])


class CategoryPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[CategoryResponse]
    next_cursor: str | None


@router.get("", summary="The tenant's product categories, by name", responses=_ERRORS)
async def read_categories(
    principal: PrincipalDep,
    services: ServicesDep,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> CategoryPage:
    page = await list_categories(
        principal, after=decode_cursor(cursor), limit=limit, unit_of_work=services.unit_of_work
    )
    return CategoryPage(
        items=[CategoryResponse.of(category) for category in page.items],
        next_cursor=encode_cursor(page.next_after),
    )


@router.get("/{category_id}", summary="One product category", responses=_ERRORS | _NOT_FOUND)
async def read_category(
    category_id: UUID, principal: PrincipalDep, services: ServicesDep, response: Response
) -> CategoryResponse:
    category = await get_category(principal, category_id, unit_of_work=services.unit_of_work)
    response.headers["ETag"] = etag(category.version)
    return CategoryResponse.of(category)


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    summary="Add a product category",
    responses=_ERRORS | _NAME_TAKEN,
)
async def add_category(
    body: CategoryRequest, principal: PrincipalDep, services: ServicesDep, response: Response
) -> CategoryResponse:
    category = await create_category(
        principal, name=body.name, unit_of_work=services.unit_of_work, clock=services.clock
    )
    response.headers["Location"] = f"{router.prefix}/{category.id}"
    response.headers["ETag"] = etag(category.version)
    return CategoryResponse.of(category)


@router.patch(
    "/{category_id}",
    summary="Rename a product category",
    responses=_ERRORS
    | _NOT_FOUND
    | _NAME_TAKEN
    | {
        412: {"description": "If-Match is stale: someone else changed the category; reload it"},
        428: {"description": "If-Match is required"},
    },
)
async def update_category(
    category_id: UUID,
    body: CategoryRequest,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> CategoryResponse:
    category = await rename_category(
        principal,
        category_id,
        name=body.name,
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    response.headers["ETag"] = etag(category.version)
    return CategoryResponse.of(category)
