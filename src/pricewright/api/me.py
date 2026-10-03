"""The signed-in user."""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.application.authentication import current_user
from pricewright.domain.users import Role

router = APIRouter(prefix="/api/v1", tags=["users"])


class UserResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    email: str
    full_name: str
    role: Role


@router.get(
    "/me",
    summary="The signed-in user",
    responses={401: {"description": "Missing or invalid access token"}},
)
async def me(principal: PrincipalDep, services: ServicesDep) -> UserResponse:
    user = await current_user(principal, unit_of_work=services.unit_of_work)
    return UserResponse(
        id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
    )
