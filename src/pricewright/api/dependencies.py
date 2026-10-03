"""What routes receive from the composition root, and who is calling (FastAPI dependencies)."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from pricewright.application.authentication import authenticate_api_key
from pricewright.application.ports import AccessTokens, Clock, PasswordHasher, UnitOfWorkFactory
from pricewright.domain.auth import AuthenticationError, Principal
from pricewright.domain.service_accounts import KEY_PREFIX


@dataclass(frozen=True, slots=True)
class Services:
    """The adapters the API's use cases run with, wired in ``main.py``."""

    unit_of_work: UnitOfWorkFactory
    hasher: PasswordHasher
    access_tokens: AccessTokens
    clock: Clock


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


# auto_error=False: a missing header becomes our own 401 Problem Details, not FastAPI's 403.
_bearer = HTTPBearer(
    auto_error=False,
    description="An access token from `POST /api/v1/auth/login`, or a service account's API key.",
)


async def current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    services: Annotated[Services, Depends(get_services)],
) -> Principal:
    """A user's access token, or a service account's API key (told apart by its prefix)."""
    if credentials is None:
        raise AuthenticationError("an access token or API key is required")
    token = credentials.credentials
    if token.startswith(KEY_PREFIX):
        return await authenticate_api_key(
            token, unit_of_work=services.unit_of_work, clock=services.clock
        )
    return services.access_tokens.read(token)


ServicesDep = Annotated[Services, Depends(get_services)]
PrincipalDep = Annotated[Principal, Depends(current_principal)]
