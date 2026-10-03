from http import HTTPStatus

import httpx
import pytest
from fastapi import FastAPI

from pricewright.api.app import create_app
from pricewright.api.health import ReadinessCheck
from tests.fakes import fake_services


async def _ok() -> None:
    return None


async def _down() -> None:
    raise ConnectionError("database unreachable")


@pytest.fixture
def app(request: pytest.FixtureRequest) -> FastAPI:
    checks: dict[str, ReadinessCheck] = getattr(request, "param", {})
    return create_app(title="test", services=fake_services(), readiness_checks=checks)


async def test_live_is_ok_without_checking_dependencies(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "ok"}


async def test_ready_without_checks_is_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "ok", "checks": {}}


@pytest.mark.parametrize(
    ("app", "expected_status", "expected_body"),
    [
        (
            {"database": _ok},
            HTTPStatus.OK,
            {"status": "ok", "checks": {"database": "ok"}},
        ),
        (
            {"database": _down, "cache": _ok},
            HTTPStatus.SERVICE_UNAVAILABLE,
            {"status": "unavailable", "checks": {"database": "unavailable", "cache": "ok"}},
        ),
    ],
    ids=["dependency-up", "dependency-down"],
    indirect=["app"],
)
async def test_ready_reports_each_dependency(
    client: httpx.AsyncClient,
    expected_status: HTTPStatus,
    expected_body: dict[str, object],
) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == expected_status
    assert response.json() == expected_body
