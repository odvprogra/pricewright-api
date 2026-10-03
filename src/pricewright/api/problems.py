"""RFC 9457 Problem Details: one error format for every failure the API returns (HANDBOOK §8).

``type`` stays ``about:blank`` (so ``title`` is the HTTP status phrase); ``code`` is the stable,
machine-readable identifier clients branch on. The OpenAPI document describes every error response
the same way, so generated clients get the real error type.
"""

from http import HTTPStatus
from typing import Any, cast

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
    StaleVersionError,
)

PROBLEM_JSON = "application/problem+json"

DOMAIN_ERROR_STATUS: dict[type[DomainError], HTTPStatus] = {
    AuthenticationError: HTTPStatus.UNAUTHORIZED,
    PermissionDeniedError: HTTPStatus.FORBIDDEN,
    NotFoundError: HTTPStatus.NOT_FOUND,
    ConflictError: HTTPStatus.CONFLICT,
    RuleViolationError: HTTPStatus.UNPROCESSABLE_CONTENT,
    StaleVersionError: HTTPStatus.PRECONDITION_FAILED,  # RFC 9110 §13.1.1
}


class FieldError(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str
    message: str


class Problem(BaseModel):
    """An RFC 9457 problem: ``code`` identifies it; ``errors`` lists the invalid fields."""

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


_SCHEMA_REF = "#/components/schemas/{model}"
# FastAPI documents its default 422 with these; they no longer describe any response.
_FASTAPI_VALIDATION_SCHEMAS = ("HTTPValidationError", "ValidationError")


def _is_error(status: str) -> bool:
    return status == "default" or status[0] in "45"


def _document_problems(spec: dict[str, Any]) -> None:
    """Describe every error response as Problem Details. Idempotent: FastAPI caches the spec."""
    schemas = spec.setdefault("components", {}).setdefault("schemas", {})
    problem = Problem.model_json_schema(ref_template=_SCHEMA_REF, mode="serialization")
    schemas.update(problem.pop("$defs", {}))
    schemas[Problem.__name__] = problem
    content = {PROBLEM_JSON: {"schema": {"$ref": _SCHEMA_REF.format(model=Problem.__name__)}}}
    for path_item in spec.get("paths", {}).values():
        for operation in path_item.values():
            for status, response in operation.get("responses", {}).items():
                if _is_error(status):
                    response["content"] = content
    for name in _FASTAPI_VALIDATION_SCHEMAS:
        schemas.pop(name, None)


def register_problem_handlers(app: FastAPI) -> None:
    """Map domain, validation and HTTP errors to Problem Details responses, and document them."""
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(HTTPException, _http_error)
    generate_spec = app.openapi

    def openapi() -> dict[str, Any]:
        spec = generate_spec()
        _document_problems(spec)
        return spec

    # Replacing the method is FastAPI's documented way to extend the spec:
    # https://fastapi.tiangolo.com/how-to/extending-openapi/
    app.openapi = openapi  # type: ignore[method-assign]
