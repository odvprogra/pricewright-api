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
        "quote_prefix": "QUO",
        "quote_validity_days": 30,
        "order_prefix": "ORD",
        "version": 1,
    }


async def test_tenant_without_a_token_is_a_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/tenant")

    assert response.status_code == 401


async def test_tenant_sends_its_version_as_an_etag(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/tenant", headers=bearer(Role.SALES_REP))

    assert response.headers["etag"] == '"1"'
    assert response.json()["version"] == 1


async def patch(
    client: httpx.AsyncClient,
    body: dict[str, object],
    *,
    role: Role = Role.ADMIN,
    if_match: str | None = '"1"',
) -> httpx.Response:
    headers = bearer(role) | ({"If-Match": if_match} if if_match is not None else {})
    return await client.patch("/api/v1/tenant", json=body, headers=headers)


async def test_admin_changes_the_tenant_with_the_current_etag(client: httpx.AsyncClient) -> None:
    response = await patch(client, {"tax_rate": "0.08", "name": "Northfield"})

    assert response.status_code == 200
    assert response.headers["etag"] == '"2"'
    assert response.json() | {"id": None} == {
        "id": None,
        "name": "Northfield",
        "currency": "USD",
        "tax_rate": "0.08",
        "approval_threshold": "0.15",
        "quote_prefix": "QUO",
        "quote_validity_days": 30,
        "order_prefix": "ORD",
        "version": 2,
    }


async def test_admin_changes_the_quote_settings(client: httpx.AsyncClient) -> None:
    response = await patch(client, {"quote_prefix": "NF", "quote_validity_days": 45})

    assert response.status_code == 200
    assert (response.json()["quote_prefix"], response.json()["quote_validity_days"]) == ("NF", 45)


async def test_admin_changes_the_order_prefix(client: httpx.AsyncClient) -> None:
    response = await patch(client, {"order_prefix": "NFO"})

    assert response.status_code == 200
    assert response.json()["order_prefix"] == "NFO"


async def test_a_second_change_with_the_old_etag_is_a_412(client: httpx.AsyncClient) -> None:
    await patch(client, {"name": "First edit"})

    response = await patch(client, {"name": "Second edit"})

    assert response.status_code == 412
    assert response.json()["code"] == "stale_version"


@pytest.mark.parametrize("if_match", [None, "*"])
async def test_changing_the_tenant_without_an_etag_is_a_428(
    client: httpx.AsyncClient, if_match: str | None
) -> None:
    response = await patch(client, {"name": "Unconditional"}, if_match=if_match)

    assert response.status_code == 428
    assert response.json()["code"] == "precondition_required"


async def test_a_sales_manager_cannot_change_the_tenant(client: httpx.AsyncClient) -> None:
    response = await patch(client, {"name": "Hijacked"}, role=Role.SALES_MANAGER)

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"currency": "EUR"},
        {"tax_rate": "1.5"},
        {"quote_prefix": "N-F"},
        {"quote_validity_days": 0},
        {"quote_validity_days": "a month"},
        {"order_prefix": "nfo"},
        {"order_prefix": "QUO"},
    ],
    ids=[
        "empty",
        "currency",
        "out-of-range",
        "prefix",
        "validity",
        "validity-type",
        "order-prefix",
        "same-prefixes",
    ],
)
async def test_invalid_changes_are_a_422(
    client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    response = await patch(client, body)

    assert response.status_code == 422
