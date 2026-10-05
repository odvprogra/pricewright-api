"""The signed-in user's tenant."""

from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Header, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pricewright.api.concurrency import etag, expected_version
from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.application.tenants import TenantChanges, change_tenant, get_tenant
from pricewright.domain.tenants import Tenant

router = APIRouter(prefix="/api/v1", tags=["tenant"])


class TenantResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    currency: str = Field(description="ISO 4217 code; every price of the tenant is in it.")
    tax_rate: Decimal = Field(description="Flat tax rate applied to quotes, e.g. 0.0725.")
    approval_threshold: Decimal = Field(
        description="Quotes whose total discount exceeds it need a manager's approval."
    )
    quote_prefix: str = Field(
        description="Starts every new quote number: NF gives NF-2026-000123.", examples=["NF"]
    )
    quote_validity_days: int = Field(
        description="How long a new quote is valid unless the rep sets another date."
    )
    version: int = Field(description="Also sent as the ETag; send it back in If-Match to update.")

    @classmethod
    def of(cls, tenant: Tenant) -> TenantResponse:
        return cls(
            id=tenant.id,
            name=tenant.name,
            currency=tenant.settings.currency,
            tax_rate=tenant.settings.tax_rate,
            approval_threshold=tenant.settings.approval_threshold,
            quote_prefix=tenant.settings.quote_prefix,
            quote_validity_days=tenant.settings.quote_validity_days,
            version=tenant.version,
        )


class TenantPatch(BaseModel):
    """Only the fields sent change. The currency cannot: every stored price is in it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = None
    tax_rate: Decimal | None = None
    approval_threshold: Decimal | None = None
    quote_prefix: str | None = Field(
        default=None,
        description="2 to 5 upper-case letters or digits, starting with a letter. Numbers new "
        "quotes only: issued numbers never change.",
        examples=["NF"],
    )
    quote_validity_days: int | None = Field(default=None, description="From 1 to 365.")

    @model_validator(mode="after")
    def _changes_something(self) -> Self:
        if self.model_fields_set == set():
            raise ValueError("send at least one field to change")
        return self


@router.get(
    "/tenant",
    summary="The signed-in user's tenant and its settings",
    responses={401: {"description": "Missing or invalid access token"}},
)
async def read_tenant(
    principal: PrincipalDep, services: ServicesDep, response: Response
) -> TenantResponse:
    tenant = await get_tenant(principal, unit_of_work=services.unit_of_work)
    response.headers["ETag"] = etag(tenant.version)
    return TenantResponse.of(tenant)


@router.patch(
    "/tenant",
    summary="Change the tenant's name, rates or quote settings (admins)",
    responses={
        401: {"description": "Missing or invalid access token"},
        403: {"description": "Only admins change the tenant (`tenant:manage`)"},
        412: {"description": "If-Match is stale: someone else changed the tenant; reload it"},
        428: {"description": "If-Match is required"},
    },
)
async def update_tenant(
    body: TenantPatch,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> TenantResponse:
    tenant = await change_tenant(
        principal,
        TenantChanges(
            name=body.name,
            tax_rate=body.tax_rate,
            approval_threshold=body.approval_threshold,
            quote_prefix=body.quote_prefix,
            quote_validity_days=body.quote_validity_days,
        ),
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    response.headers["ETag"] = etag(tenant.version)
    return TenantResponse.of(tenant)
