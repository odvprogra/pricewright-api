"""Service accounts and their API keys (admins: ``service_accounts:manage``)."""

from datetime import datetime
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from pricewright.api.dependencies import PrincipalDep, ServicesDep
from pricewright.api.pagination import DEFAULT_LIMIT, Cursor, Limit, decode_cursor, encode_cursor
from pricewright.application.service_accounts import (
    create_service_account,
    get_service_account,
    issue_api_key,
    list_api_keys,
    list_service_accounts,
    revoke_api_key,
)
from pricewright.domain.auth import GRANTABLE_SCOPES, Permission
from pricewright.domain.service_accounts import MAX_NAME_LENGTH, ApiKey, ServiceAccount

router = APIRouter(prefix="/api/v1/service-accounts", tags=["service accounts"])

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"description": "Missing or invalid credentials"},
    403: {"description": "Only admins manage service accounts (`service_accounts:manage`)"},
}
_NOT_FOUND: dict[int | str, dict[str, object]] = {
    404: {"description": "No such service account or key in this tenant (ADR-0009)"}
}


class ServiceAccountRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(max_length=MAX_NAME_LENGTH, examples=["erp-mcp-server"])
    scopes: list[Permission] = Field(
        description="Permissions the account acts with; never administrative ones."
    )


# Open-ended (ADR-0015): permissions are added as the API grows.
Scope = Annotated[str, Field(examples=sorted(GRANTABLE_SCOPES))]


class ServiceAccountResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    scopes: list[Scope] = Field(description="New scopes may appear; handle unknown ones.")
    is_active: bool

    @classmethod
    def of(cls, account: ServiceAccount) -> ServiceAccountResponse:
        return cls(
            id=account.id,
            name=account.name,
            scopes=sorted(account.scopes),
            is_active=account.is_active,
        )


class ServiceAccountPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[ServiceAccountResponse]
    next_cursor: str | None


class ApiKeyRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expires_at: AwareDatetime | None = Field(
        default=None, description="Optional expiry, with a time zone. Omit for a key that lasts."
    )


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    hint: str = Field(description="The key's first characters, to tell keys apart.")
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None = Field(description="Updated at most once an hour.")

    @classmethod
    def of(cls, key: ApiKey) -> ApiKeyResponse:
        return cls(
            id=key.id,
            hint=key.hint,
            created_at=key.created_at,
            expires_at=key.expires_at,
            last_used_at=key.last_used_at,
        )


class IssuedApiKeyResponse(ApiKeyResponse):
    key: str = Field(
        description="Send it as `Authorization: Bearer <key>`. Shown once: it is not stored."
    )


class ApiKeyList(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[ApiKeyResponse]


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    summary="Add a service account",
    responses=_ERRORS | {409: {"description": "The tenant already has an account with that name"}},
)
async def add_service_account(
    body: ServiceAccountRequest, principal: PrincipalDep, services: ServicesDep, response: Response
) -> ServiceAccountResponse:
    account = await create_service_account(
        principal,
        name=body.name,
        scopes=frozenset(body.scopes),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    response.headers["Location"] = f"{router.prefix}/{account.id}"
    return ServiceAccountResponse.of(account)


@router.get("", summary="The tenant's service accounts, oldest first", responses=_ERRORS)
async def read_service_accounts(
    principal: PrincipalDep,
    services: ServicesDep,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> ServiceAccountPage:
    page = await list_service_accounts(
        principal, after=decode_cursor(cursor), limit=limit, unit_of_work=services.unit_of_work
    )
    return ServiceAccountPage(
        items=[ServiceAccountResponse.of(account) for account in page.items],
        next_cursor=encode_cursor(page.next_after),
    )


@router.get("/{account_id}", summary="One service account", responses=_ERRORS | _NOT_FOUND)
async def read_service_account(
    account_id: UUID, principal: PrincipalDep, services: ServicesDep
) -> ServiceAccountResponse:
    account = await get_service_account(principal, account_id, unit_of_work=services.unit_of_work)
    return ServiceAccountResponse.of(account)


@router.post(
    "/{account_id}/keys",
    status_code=HTTPStatus.CREATED,
    summary="Issue an API key (shown once)",
    responses=_ERRORS
    | _NOT_FOUND
    | {409: {"description": "The account already has two active keys; revoke one first"}},
)
async def add_api_key(
    account_id: UUID,
    body: ApiKeyRequest,
    principal: PrincipalDep,
    services: ServicesDep,
    response: Response,
) -> IssuedApiKeyResponse:
    issued = await issue_api_key(
        principal,
        account_id,
        expires_at=body.expires_at,
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
    response.headers["Cache-Control"] = "no-store"  # the response carries a secret
    response.headers["Location"] = f"{router.prefix}/{account_id}/keys/{issued.key.id}"
    return IssuedApiKeyResponse(**ApiKeyResponse.of(issued.key).model_dump(), key=issued.secret)


@router.get(
    "/{account_id}/keys",
    summary="The account's active API keys (never the keys themselves)",
    responses=_ERRORS | _NOT_FOUND,
)
async def read_api_keys(
    account_id: UUID, principal: PrincipalDep, services: ServicesDep
) -> ApiKeyList:
    keys = await list_api_keys(principal, account_id, unit_of_work=services.unit_of_work)
    return ApiKeyList(items=[ApiKeyResponse.of(key) for key in keys])


@router.delete(
    "/{account_id}/keys/{key_id}",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Revoke an API key for good",
    responses=_ERRORS | _NOT_FOUND,
)
async def delete_api_key(
    account_id: UUID, key_id: UUID, principal: PrincipalDep, services: ServicesDep
) -> None:
    await revoke_api_key(
        principal, account_id, key_id, unit_of_work=services.unit_of_work, clock=services.clock
    )
