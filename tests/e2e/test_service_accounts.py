"""Service account administration over HTTP, with in-memory adapters."""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest

from pricewright.api.app import create_app
from pricewright.domain.auth import GRANTABLE_SCOPES, Principal
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeAccessTokens, InMemoryDatabase, fake_services

NORTHFIELD = Tenant.register(name="Northfield", settings=TenantSettings("USD", Decimal(0)))
LARKSPUR = Tenant.register(name="Larkspur", settings=TenantSettings("USD", Decimal(0)))
COPILOT = {"name": "ops-copilot", "scopes": ["tenant:read"]}


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    database = InMemoryDatabase(tenants={NORTHFIELD.id: NORTHFIELD, LARKSPUR.id: LARKSPUR})
    app = create_app(title="test", services=fake_services(database))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def bearer(tenant: Tenant = NORTHFIELD, role: Role = Role.ADMIN) -> dict[str, str]:
    token = FakeAccessTokens().issue(Principal(tenant.id, uuid.uuid7(), role)).token
    return {"Authorization": f"Bearer {token}"}


async def create_copilot(client: httpx.AsyncClient) -> str:
    response = await client.post("/api/v1/service-accounts", json=COPILOT, headers=bearer())
    account_id: str = response.json()["id"]
    return account_id


async def test_an_admin_adds_and_lists_service_accounts(client: httpx.AsyncClient) -> None:
    created = await client.post("/api/v1/service-accounts", json=COPILOT, headers=bearer())
    listed = await client.get("/api/v1/service-accounts", headers=bearer())
    read = await client.get(created.headers["location"], headers=bearer())

    assert created.status_code == 201
    assert created.headers["location"] == f"/api/v1/service-accounts/{created.json()['id']}"
    assert created.json() | {"id": None} == {
        "id": None,
        "name": "ops-copilot",
        "scopes": ["tenant:read"],
        "is_active": True,
    }
    assert listed.json() == {"items": [created.json()], "next_cursor": None}
    assert read.json() == created.json()


@pytest.mark.parametrize(
    "body",
    [
        {"name": "admin-bot", "scopes": ["users:manage"]},
        {"name": "typo-bot", "scopes": ["quotes:everything"]},
        {"name": "", "scopes": []},
    ],
    ids=["administrative-scope", "unknown-scope", "blank-name"],
)
async def test_invalid_service_accounts_are_a_422(
    client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    response = await client.post("/api/v1/service-accounts", json=body, headers=bearer())

    assert response.status_code == 422


async def test_a_duplicate_name_in_the_tenant_is_a_409(client: httpx.AsyncClient) -> None:
    await create_copilot(client)

    response = await client.post("/api/v1/service-accounts", json=COPILOT, headers=bearer())

    assert response.status_code == 409


async def test_an_issued_key_is_shown_once_and_listed_by_its_hint(
    client: httpx.AsyncClient,
) -> None:
    account_id = await create_copilot(client)

    issued = await client.post(
        f"/api/v1/service-accounts/{account_id}/keys", json={}, headers=bearer()
    )
    listed = await client.get(f"/api/v1/service-accounts/{account_id}/keys", headers=bearer())

    assert issued.status_code == 201
    assert issued.headers["cache-control"] == "no-store"
    key = issued.json()["key"]
    assert key.startswith("pwk_")
    assert issued.json()["hint"] == key[:8]
    [listed_key] = listed.json()["items"]
    assert listed_key["hint"] == key[:8]
    assert key not in listed.text


async def test_a_third_active_key_is_a_409_until_one_is_revoked(client: httpx.AsyncClient) -> None:
    account_id = await create_copilot(client)
    keys_url = f"/api/v1/service-accounts/{account_id}/keys"
    first = await client.post(keys_url, json={}, headers=bearer())
    await client.post(keys_url, json={}, headers=bearer())

    third = await client.post(keys_url, json={}, headers=bearer())
    revoked = await client.delete(f"{keys_url}/{first.json()['id']}", headers=bearer())
    after_revoking = await client.post(keys_url, json={}, headers=bearer())

    assert third.status_code == 409
    assert third.json()["code"] == "too_many_api_keys"
    assert revoked.status_code == 204
    assert after_revoking.status_code == 201


@pytest.mark.parametrize(
    "expires_at", ["2020-01-01T00:00:00Z", "2030-01-01T00:00:00"], ids=["past", "no-time-zone"]
)
async def test_an_invalid_expiry_is_a_422(client: httpx.AsyncClient, expires_at: str) -> None:
    account_id = await create_copilot(client)

    response = await client.post(
        f"/api/v1/service-accounts/{account_id}/keys",
        json={"expires_at": expires_at},
        headers=bearer(),
    )

    assert response.status_code == 422


async def test_another_tenants_service_account_is_a_404(client: httpx.AsyncClient) -> None:
    account_id = await create_copilot(client)

    read = await client.get(f"/api/v1/service-accounts/{account_id}", headers=bearer(LARKSPUR))
    issue = await client.post(
        f"/api/v1/service-accounts/{account_id}/keys", json={}, headers=bearer(LARKSPUR)
    )

    assert read.status_code == issue.status_code == 404


async def test_a_sales_manager_cannot_manage_service_accounts(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/service-accounts", headers=bearer(role=Role.SALES_MANAGER))

    assert response.status_code == 403


def test_scopes_are_documented_as_open_ended_values() -> None:
    spec = create_app(title="test", services=fake_services()).openapi()

    scope = spec["components"]["schemas"]["ServiceAccountResponse"]["properties"]["scopes"]["items"]
    assert "enum" not in scope  # a new permission must not break generated clients (ADR-0015)
    assert set(scope["examples"]) == GRANTABLE_SCOPES
