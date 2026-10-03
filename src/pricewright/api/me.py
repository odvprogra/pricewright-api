"""The signed-in user."""

from fastapi import APIRouter

from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.api.users import UserResponse
from pricewright.application.authentication import current_user

router = APIRouter(prefix="/api/v1", tags=["users"])


@router.get(
    "/me",
    summary="The signed-in user",
    responses={401: {"description": "Missing or invalid access token"}},
)
async def me(principal: PrincipalDep, services: ServicesDep) -> UserResponse:
    return UserResponse.of(await current_user(principal, unit_of_work=services.unit_of_work))
