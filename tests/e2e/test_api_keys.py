"""Calling the API with a service account's key, with in-memory adapters."""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest

from pricewright.api.app import create_app
from pricewright.domain.auth import Principal
from pricewright.domain.service_accounts import new_api_key
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeAccessTokens, InMemoryDatabase, fake_services

NORTHFIELD = Tenant.register(name="Northfield", settings=TenantSettings("USD", Decimal(0)))
ADMIN = {
    "Authorization": "Bearer "
    + FakeAccessTokens().issue(Principal(NORTHFIELD.id, uuid.uuid7(), Role.ADMIN)).token
}


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    database = InMemoryDatabase(tenants={NORTHFIELD.id: NORTHFIELD})
    app = create_app(title="test", services=fake_services(database))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def issue_key(client: httpx.AsyncClient) -> tuple[str, str, str]:
    """An ops-copilot account with tenant:read; returns its id, the key id and the key."""
    account = await client.post(
        "/api/v1/service-accounts",
        json={"name": "ops-copilot", "scopes": ["tenant:read"]},
        headers=ADMIN,
    )
    account_id = account.json()["id"]
    key = await client.post(f"/api/v1/service-accounts/{account_id}/keys", json={}, headers=ADMIN)
    return account_id, key.json()["id"], key.json()["key"]


def with_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def test_a_key_reaches_what_its_scopes_allow(client: httpx.AsyncClient) -> None:
    _, _, key = await issue_key(client)

    tenant = await client.get("/api/v1/tenant", headers=with_key(key))
    users = await client.get("/api/v1/users", headers=with_key(key))

    assert tenant.status_code == 200
    assert tenant.json()["id"] == str(NORTHFIELD.id)
    assert users.status_code == 403  # users:manage is never grantable


async def test_a_key_has_no_user_profile(client: httpx.AsyncClient) -> None:
    _, _, key = await issue_key(client)

    response = await client.get("/api/v1/me", headers=with_key(key))

    assert response.status_code == 403


async def test_a_revoked_key_is_a_401(client: httpx.AsyncClient) -> None:
    account_id, key_id, key = await issue_key(client)

    await client.delete(f"/api/v1/service-accounts/{account_id}/keys/{key_id}", headers=ADMIN)
    response = await client.get("/api/v1/tenant", headers=with_key(key))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "key", [new_api_key(), "pwk_" + "x" * 46, "pwk_short"], ids=["unknown", "bad-checksum", "short"]
)
async def test_keys_that_were_not_issued_are_a_401(client: httpx.AsyncClient, key: str) -> None:
    response = await client.get("/api/v1/tenant", headers=with_key(key))

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid API key"
