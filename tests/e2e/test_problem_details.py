from http import HTTPStatus

import httpx
import pytest
from fastapi import FastAPI

from pricewright.api.app import create_app
from pricewright.api.problems import PROBLEM_JSON
from pricewright.domain.auth import PermissionDeniedError
from pricewright.domain.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    RuleViolationError,
)
from tests.fakes import fake_services

FORBIDDEN_ID = 403
NOT_FOUND_ID = 404
CONFLICT_ID = 409
RULE_ID = 422
UNMAPPED_ID = 418
CRASH_ID = 500


@pytest.fixture
def app() -> FastAPI:
    app = create_app(title="test", services=fake_services())

    @app.get("/orders/{order_id}", responses={404: {"description": "No such order"}})
    async def get_order(order_id: int) -> dict[str, int]:
        if order_id == FORBIDDEN_ID:
            raise PermissionDeniedError("this action needs the orders:manage permission")
        if order_id == NOT_FOUND_ID:
            raise NotFoundError(f"Order {order_id} does not exist.")
        if order_id == CONFLICT_ID:
            raise ConflictError("The order was modified by someone else.")
        if order_id == RULE_ID:
            raise RuleViolationError("Discount exceeds the approval threshold.")
        if order_id == UNMAPPED_ID:
            raise DomainError("A business rule without a specific mapping.")
        if order_id == CRASH_ID:
            raise RuntimeError("connection string postgres://secret@db leaked")
        return {"id": order_id}

    return app


@pytest.mark.parametrize(
    ("order_id", "status", "code"),
    [
        (FORBIDDEN_ID, HTTPStatus.FORBIDDEN, "permission_denied"),
        (NOT_FOUND_ID, HTTPStatus.NOT_FOUND, "not_found"),
        (CONFLICT_ID, HTTPStatus.CONFLICT, "conflict"),
        (RULE_ID, HTTPStatus.UNPROCESSABLE_CONTENT, "rule_violation"),
        (UNMAPPED_ID, HTTPStatus.UNPROCESSABLE_CONTENT, "domain_error"),
    ],
)
async def test_domain_errors_become_problem_details(
    client: httpx.AsyncClient, order_id: int, status: HTTPStatus, code: str
) -> None:
    response = await client.get(f"/orders/{order_id}")

    assert response.status_code == status
    assert response.headers["content-type"] == PROBLEM_JSON
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == status.phrase
    assert body["status"] == status.value
    assert body["code"] == code
    assert body["instance"] == f"/orders/{order_id}"
    assert body["detail"]


async def test_validation_errors_list_each_invalid_field(client: httpx.AsyncClient) -> None:
    response = await client.get("/orders/not-a-number")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["code"] == "validation_error"
    assert [error["field"] for error in body["errors"]] == ["path.order_id"]


async def test_unknown_route_is_a_not_found_problem(client: httpx.AsyncClient) -> None:
    response = await client.get("/nowhere")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "not_found"


async def test_wrong_method_keeps_the_allow_header(client: httpx.AsyncClient) -> None:
    response = await client.post("/health/live")

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.json()["code"] == "method_not_allowed"
    assert "GET" in response.headers["allow"]


async def test_unhandled_error_is_a_generic_500_without_internals(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(f"/orders/{CRASH_ID}")

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "internal_error"
    assert "secret" not in response.text
    assert response.headers["x-request-id"]


def test_openapi_documents_every_error_as_problem_details(app: FastAPI) -> None:
    spec = app.openapi()

    responses = spec["paths"]["/orders/{order_id}"]["get"]["responses"]
    problem = {PROBLEM_JSON: {"schema": {"$ref": "#/components/schemas/Problem"}}}
    assert responses["404"]["content"] == problem
    assert responses["422"]["content"] == problem  # FastAPI's default validation error
    assert "application/json" in responses["200"]["content"]
    schemas = spec["components"]["schemas"]
    assert {"Problem", "FieldError"} <= schemas.keys()
    assert "HTTPValidationError" not in schemas


def test_openapi_documents_routes_added_after_the_first_render(app: FastAPI) -> None:
    app.openapi()

    @app.delete("/orders/{order_id}", status_code=HTTPStatus.NO_CONTENT)
    async def delete_order(order_id: int) -> None: ...

    responses = app.openapi()["paths"]["/orders/{order_id}"]["delete"]["responses"]
    assert responses["422"]["content"] == {
        PROBLEM_JSON: {"schema": {"$ref": "#/components/schemas/Problem"}}
    }
