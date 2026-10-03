"""RFC 9457 Problem Details: one error format for every failure the API returns (HANDBOOK §8).

``type`` stays ``about:blank`` (so ``title`` is the HTTP status phrase); ``code`` is the stable,
machine-readable identifier clients branch on.
"""

from http import HTTPStatus
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException

from pricewright.domain.auth import AuthenticationError, PermissionDeniedError
from pricewright.domain.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    RuleViolationError,
)

PROBLEM_JSON = "application/problem+json"

DOMAIN_ERROR_STATUS: dict[type[DomainError], HTTPStatus] = {
    AuthenticationError: HTTPStatus.UNAUTHORIZED,
    PermissionDeniedError: HTTPStatus.FORBIDDEN,
    NotFoundError: HTTPStatus.NOT_FOUND,
    ConflictError: HTTPStatus.CONFLICT,
    RuleViolationError: HTTPStatus.UNPROCESSABLE_CONTENT,
}


class FieldError(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str
    message: str


class Problem(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    code: str
    errors: list[FieldError] | None = None


def problem_response(
    status: HTTPStatus,
    *,
    code: str,
    instance: str,
    detail: str | None = None,
    errors: list[FieldError] | None = None,
) -> JSONResponse:
    problem = Problem(
        title=status.phrase,
        status=status.value,
        detail=detail,
        instance=instance,
        code=code,
        errors=errors,
    )
    return JSONResponse(
        problem.model_dump(exclude_none=True), status_code=status.value, media_type=PROBLEM_JSON
    )


def _status_for(error: DomainError) -> HTTPStatus:
    for cls in type(error).__mro__:
        if cls in DOMAIN_ERROR_STATUS:
            return DOMAIN_ERROR_STATUS[cls]
    return HTTPStatus.UNPROCESSABLE_CONTENT


# Starlette types every handler as (Request, Exception); each one is registered for a single type.


async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
    error = cast(DomainError, exc)
    status = _status_for(error)
    response = problem_response(
        status, code=error.code, instance=request.url.path, detail=str(error) or None
    )
    if status is HTTPStatus.UNAUTHORIZED:
        response.headers["WWW-Authenticate"] = "Bearer"  # RFC 6750 §3
    return response


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    errors = [
        FieldError(field=".".join(str(part) for part in error["loc"]), message=error["msg"])
        for error in cast(RequestValidationError, exc).errors()
    ]
    return problem_response(
        HTTPStatus.UNPROCESSABLE_CONTENT,
        code="validation_error",
        instance=request.url.path,
        detail="The request is not valid.",
        errors=errors,
    )


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    error = cast(HTTPException, exc)
    status = HTTPStatus(error.status_code)
    detail = error.detail if error.detail != status.phrase else None
    response = problem_response(
        status, code=status.name.lower(), instance=request.url.path, detail=detail
    )
    if error.headers:
        response.headers.update(error.headers)
    return response


def register_problem_handlers(app: FastAPI) -> None:
    """Map domain, validation and HTTP errors to Problem Details responses."""
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(HTTPException, _http_error)
