"""A tenant's users (admins: ``users:manage``)."""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.api.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, encode_cursor
from pricewright.application.users import get_user, list_users
from pricewright.domain.users import Role, User

router = APIRouter(prefix="/api/v1/users", tags=["users"])


class UserResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    locked: bool

    @classmethod
    def of(cls, user: User) -> UserResponse:
        return cls(
            id=user.id,
            tenant_id=user.tenant_id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
            locked=user.is_locked,
        )


class UserPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[UserResponse]
    next_cursor: str | None


_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"description": "Missing or invalid access token"},
    403: {"description": "Only admins manage users (`users:manage`)"},
}


@router.get("", summary="The tenant's users, oldest first", responses=_ERRORS)
async def read_users(
    principal: PrincipalDep,
    services: ServicesDep,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> UserPage:
    page = await list_users(
        principal, after=decode_cursor(cursor), limit=limit, unit_of_work=services.unit_of_work
    )
    return UserPage(
        items=[UserResponse.of(user) for user in page.items],
        next_cursor=encode_cursor(page.next_after),
    )


@router.get(
    "/{user_id}",
    summary="One user of the tenant",
    responses=_ERRORS | {404: {"description": "No such user in this tenant (ADR-0009)"}},
)
async def read_user(user_id: UUID, principal: PrincipalDep, services: ServicesDep) -> UserResponse:
    return UserResponse.of(await get_user(principal, user_id, unit_of_work=services.unit_of_work))
