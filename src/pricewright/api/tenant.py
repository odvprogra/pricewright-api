"""The signed-in user's tenant."""

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.application.tenants import get_tenant
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

    @classmethod
    def of(cls, tenant: Tenant) -> TenantResponse:
        return cls(
            id=tenant.id,
            name=tenant.name,
            currency=tenant.settings.currency,
            tax_rate=tenant.settings.tax_rate,
            approval_threshold=tenant.settings.approval_threshold,
        )


@router.get(
    "/tenant",
    summary="The signed-in user's tenant and its settings",
    responses={401: {"description": "Missing or invalid access token"}},
)
async def read_tenant(principal: PrincipalDep, services: ServicesDep) -> TenantResponse:
    return TenantResponse.of(await get_tenant(principal, unit_of_work=services.unit_of_work))
