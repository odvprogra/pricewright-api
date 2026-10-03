"""Signing in, refreshing the session and signing out (ADR-0007)."""

from http import HTTPStatus
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from pricewright.api.dependencies import ServicesDep
from pricewright.application.authentication import (
    Credentials,
    TokenPair,
    log_in,
    log_out,
    refresh_session,
)
from pricewright.domain.users import MAX_EMAIL_LENGTH

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Generous, but bounded: an unbounded password would let a caller make hashing arbitrarily slow.
MAX_PASSWORD_INPUT_LENGTH = 1024
MAX_REFRESH_TOKEN_LENGTH = 100


class LoginRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    email: str = Field(max_length=MAX_EMAIL_LENGTH, examples=["avery@northfield.example"])
    password: SecretStr = Field(max_length=MAX_PASSWORD_INPUT_LENGTH)


class RefreshTokenRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    refresh_token: SecretStr = Field(max_length=MAX_REFRESH_TOKEN_LENGTH)


class TokenResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_token: str
    # The field name and value come from RFC 6749 §5.1 and RFC 6750; neither is a secret.
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105
    expires_in: int = Field(description="Seconds until the access token expires.")
    refresh_token: str = Field(
        description="Single use: trade it at `/auth/refresh` for a new pair. Reusing one ends the "
        "session."
    )


def _token_response(pair: TokenPair, response: Response) -> TokenResponse:
    # Tokens must never be cached by the client or a proxy (RFC 6749 §5.1).
    response.headers["Cache-Control"] = "no-store"
    return TokenResponse(
        access_token=pair.access.token,
        expires_in=pair.access.expires_in,
        refresh_token=pair.refresh_token,
    )


@router.post(
    "/login",
    summary="Sign in with an email and a password",
    responses={401: {"description": "Wrong email or password, or the account cannot sign in"}},
)
async def login(body: LoginRequest, services: ServicesDep, response: Response) -> TokenResponse:
    pair = await log_in(
        Credentials(email=body.email, password=body.password.get_secret_value()),
        unit_of_work=services.unit_of_work,
        hasher=services.hasher,
        access_tokens=services.access_tokens,
        clock=services.clock,
    )
    return _token_response(pair, response)


@router.post(
    "/refresh",
    summary="Trade a refresh token for a new access and refresh token",
    responses={401: {"description": "Unknown, expired, revoked or reused refresh token"}},
)
async def refresh(
    body: RefreshTokenRequest, services: ServicesDep, response: Response
) -> TokenResponse:
    pair = await refresh_session(
        body.refresh_token.get_secret_value(),
        unit_of_work=services.unit_of_work,
        access_tokens=services.access_tokens,
        clock=services.clock,
    )
    return _token_response(pair, response)


@router.post(
    "/logout",
    summary="End the session a refresh token belongs to",
    status_code=HTTPStatus.NO_CONTENT,
)
async def logout(body: RefreshTokenRequest, services: ServicesDep) -> None:
    await log_out(
        body.refresh_token.get_secret_value(),
        unit_of_work=services.unit_of_work,
        clock=services.clock,
    )
