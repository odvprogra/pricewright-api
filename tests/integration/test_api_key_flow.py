"""A service account's key against the fully wired application."""

import httpx
import pytest

from tests.integration.data import ADMIN_EMAIL, ADMIN_PASSWORD

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("session_factory")]


async def test_an_admin_issues_a_key_that_works_until_revoked(
    northfield_client: httpx.AsyncClient,
) -> None:
    client = northfield_client
    login = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    admin = {"Authorization": f"Bearer {login.json()['access_token']}"}
    account = await client.post(
        "/api/v1/service-accounts",
        json={"name": "ops-copilot", "scopes": ["tenant:read"]},
        headers=admin,
    )
    keys_url = f"/api/v1/service-accounts/{account.json()['id']}/keys"
    issued = await client.post(keys_url, json={}, headers=admin)
    service = {"Authorization": f"Bearer {issued.json()['key']}"}

    allowed = await client.get("/api/v1/tenant", headers=service)
    denied = await client.get("/api/v1/users", headers=service)
    listed = await client.get(keys_url, headers=admin)
    await client.delete(f"{keys_url}/{issued.json()['id']}", headers=admin)
    revoked = await client.get("/api/v1/tenant", headers=service)

    assert (allowed.status_code, denied.status_code, revoked.status_code) == (200, 403, 401)
    assert listed.json()["items"][0]["last_used_at"] is not None
