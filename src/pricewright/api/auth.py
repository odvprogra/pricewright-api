"""Signing in (ADR-0007)."""

from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from pricewright.api.dependencies import ServicesDep
from pricewright.application.authentication import Credentials, log_in
from pricewright.domain.users import MAX_EMAIL_LENGTH

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Generous, but bounded: an unbounded password would let a caller make hashing arbitrarily slow.
MAX_PASSWORD_INPUT_LENGTH = 1024


class LoginRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    email: str = Field(max_length=MAX_EMAIL_LENGTH, examples=["avery@northfield.example"])
    password: SecretStr = Field(max_length=MAX_PASSWORD_INPUT_LENGTH)


class TokenResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_token: str
    # The field name and value come from RFC 6749 §5.1 and RFC 6750; neither is a secret.
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105
    expires_in: int = Field(description="Seconds until the access token expires.")


@router.post(
    "/login",
    summary="Sign in with an email and a password",
    responses={401: {"description": "Wrong email or password, or the account cannot sign in"}},
)
async def login(body: LoginRequest, services: ServicesDep, response: Response) -> TokenResponse:
    issued = await log_in(
        Credentials(email=body.email, password=body.password.get_secret_value()),
        unit_of_work=services.unit_of_work,
        hasher=services.hasher,
        access_tokens=services.access_tokens,
    )
    # Tokens must never be cached by the client or a proxy (RFC 6749 §5.1).
    response.headers["Cache-Control"] = "no-store"
    return TokenResponse(access_token=issued.token, expires_in=issued.expires_in)
