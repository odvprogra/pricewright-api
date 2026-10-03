"""The tenant endpoint over HTTP, with in-memory adapters."""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest

from pricewright.api.app import create_app
from pricewright.domain.auth import Principal
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeAccessTokens, InMemoryDatabase, fake_services

NORTHFIELD = Tenant.register(
    name="Northfield Supply", settings=TenantSettings("USD", Decimal("0.0725"))
)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    database = InMemoryDatabase(tenants={NORTHFIELD.id: NORTHFIELD})
    app = create_app(title="test", services=fake_services(database))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def bearer(role: Role) -> dict[str, str]:
    token = FakeAccessTokens().issue(Principal(NORTHFIELD.id, uuid.uuid7(), role)).token
    return {"Authorization": f"Bearer {token}"}


async def test_tenant_returns_the_settings_with_rates_as_strings(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/tenant", headers=bearer(Role.SALES_REP))

    assert response.status_code == 200
    assert response.json() == {
        "id": str(NORTHFIELD.id),
        "name": "Northfield Supply",
        "currency": "USD",
        "tax_rate": "0.0725",
        "approval_threshold": "0.15",
    }


async def test_tenant_without_a_token_is_a_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/tenant")

    assert response.status_code == 401
