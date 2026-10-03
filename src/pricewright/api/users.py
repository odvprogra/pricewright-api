"""A tenant's users (admins: ``users:manage``)."""

from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from pricewright.api.auth import MAX_PASSWORD_INPUT_LENGTH
from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.api.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, encode_cursor
from pricewright.application.users import NewUser, create_user, get_user, list_users
from pricewright.domain.users import MAX_EMAIL_LENGTH, MAX_NAME_LENGTH, Role, User

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


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    email: str = Field(max_length=MAX_EMAIL_LENGTH, examples=["blair@northfield.example"])
    full_name: str = Field(max_length=MAX_NAME_LENGTH)
    role: Role
    password: SecretStr = Field(
        max_length=MAX_PASSWORD_INPUT_LENGTH,
        description="Initial password, 15 to 128 characters (NIST SP 800-63B-4).",
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


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    summary="Add a user to the tenant",
    responses=_ERRORS | {409: {"description": "The email is already registered in some tenant"}},
)
async def add_user(
    body: CreateUserRequest, principal: PrincipalDep, services: ServicesDep, response: Response
) -> UserResponse:
    user = await create_user(
        principal,
        NewUser(
            email=body.email,
            full_name=body.full_name,
            role=body.role,
            password=body.password.get_secret_value(),
        ),
        unit_of_work=services.unit_of_work,
        hasher=services.hasher,
    )
    response.headers["Location"] = f"{router.prefix}/{user.id}"
    return UserResponse.of(user)
