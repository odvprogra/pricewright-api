"""A tenant's users (admins: ``users:manage``)."""

from http import HTTPStatus
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from pricewright.api.auth import MAX_PASSWORD_INPUT_LENGTH
from pricewright.api.concurrency import etag, expected_version
from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.api.idempotency import (
    IdempotencyKey,
    idempotency_errors,
    idempotent_request,
    mark_replayed,
)
from pricewright.api.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, encode_cursor
from pricewright.application.users import (
    NewUser,
    UserChanges,
    change_user,
    create_user,
    get_user,
    list_users,
    unlock_user,
)
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
    locked: bool = Field(description="Too many failed sign-ins; an admin can unlock the user.")
    version: int = Field(description="Also sent as the ETag; send it back in If-Match to update.")

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
            version=user.version,
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


class UserPatch(BaseModel):
    """Only the fields sent change. The email is the sign-in identity and cannot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    full_name: str | None = Field(default=None, max_length=MAX_NAME_LENGTH)
    role: Role | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _changes_something(self) -> Self:
        if self.model_fields_set == set():
            raise ValueError("send at least one field to change")
        return self


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
async def read_user(
    user_id: UUID, principal: PrincipalDep, services: ServicesDep, response: Response
) -> UserResponse:
    user = await get_user(principal, user_id, unit_of_work=services.unit_of_work)
    response.headers["ETag"] = etag(user.version)
    return UserResponse.of(user)


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    summary="Add a user to the tenant",
    responses=idempotency_errors(
        _ERRORS | {409: {"description": "The email is already registered in some tenant"}}
    ),
)
async def add_user(
    body: CreateUserRequest,
    principal: PrincipalDep,
    services: ServicesDep,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> UserResponse:
    created = await create_user(
        principal,
        NewUser(
            email=body.email,
            full_name=body.full_name,
            role=body.role,
            password=body.password.get_secret_value(),
        ),
        unit_of_work=services.unit_of_work,
        hasher=services.hasher,
        clock=services.clock,
        idempotency=idempotent_request(idempotency_key, request, body),
    )
    user = created.value
    mark_replayed(response, created.replayed)
    response.headers["Location"] = f"{router.prefix}/{user.id}"
    response.headers["ETag"] = etag(user.version)
    return UserResponse.of(user)


@router.patch(
    "/{user_id}",
    summary="Rename a user, change their role, or (de)activate them",
    responses=_ERRORS
    | {
        404: {"description": "No such user in this tenant (ADR-0009)"},
        409: {"description": "The change would leave the tenant without an active admin"},
        412: {"description": "If-Match is stale: someone else changed the user; reload it"},
        428: {"description": "If-Match is required"},
    },
)
async def update_user(
    user_id: UUID,
    body: UserPatch,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> UserResponse:
    user = await change_user(
        principal,
        user_id,
        UserChanges(full_name=body.full_name, role=body.role, is_active=body.is_active),
        expected_version=expected_version(if_match),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    response.headers["ETag"] = etag(user.version)
    return UserResponse.of(user)


@router.post(
    "/{user_id}/unlock",
    summary="Let a user locked out by failed sign-ins try again",
    responses=_ERRORS | {404: {"description": "No such user in this tenant (ADR-0009)"}},
)
async def unlock(
    user_id: UUID, principal: PrincipalDep, services: ServicesDep, response: Response
) -> UserResponse:
    user = await unlock_user(
        principal, user_id, unit_of_work=services.unit_of_work, clock=services.clock
    )
    response.headers["ETag"] = etag(user.version)
    return UserResponse.of(user)
