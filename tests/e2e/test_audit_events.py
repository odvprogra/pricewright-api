"""Reading the audit trail over HTTP, with in-memory adapters (ADR-0013)."""

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

NORTHFIELD = Tenant.register(name="Northfield", settings=TenantSettings("USD", Decimal(0)))
LARKSPUR = Tenant.register(name="Larkspur", settings=TenantSettings("USD", Decimal(0)))
AVERY = Principal(NORTHFIELD.id, uuid.uuid7(), Role.ADMIN)
BLAIR = {
    "email": "blair@northfield.example",
    "full_name": "Blair",
    "role": "sales_rep",
    "password": "blair's initial passphrase",
}


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    database = InMemoryDatabase(tenants={NORTHFIELD.id: NORTHFIELD, LARKSPUR.id: LARKSPUR})
    app = create_app(title="test", services=fake_services(database))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def bearer(principal: Principal) -> dict[str, str]:
    return {"Authorization": f"Bearer {FakeAccessTokens().issue(principal).token}"}


async def add_and_promote_blair(client: httpx.AsyncClient) -> str:
    created = await client.post(
        "/api/v1/users", json=BLAIR, headers=bearer(AVERY) | {"X-Request-ID": "req-create"}
    )
    blair_id: str = created.json()["id"]
    await client.patch(
        f"/api/v1/users/{blair_id}",
        json={"role": "sales_manager"},
        headers=bearer(AVERY) | {"If-Match": '"1"', "X-Request-ID": "req-promote"},
    )
    return blair_id


async def audit(client: httpx.AsyncClient, **params: str | int) -> httpx.Response:
    return await client.get("/api/v1/audit-events", params=params, headers=bearer(AVERY))


async def test_audit_trail_shows_who_changed_what_newest_first(client: httpx.AsyncClient) -> None:
    blair_id = await add_and_promote_blair(client)

    response = await audit(client)

    assert response.status_code == 200
    promoted, created = response.json()["items"]
    assert promoted == {
        "id": promoted["id"],
        "occurred_at": promoted["occurred_at"],
        "actor_type": "user",
        "actor_id": str(AVERY.subject_id),
        "action": "user.updated",
        "resource_type": "user",
        "resource_id": blair_id,
        "changes": {"role": {"before": "sales_rep", "after": "sales_manager"}},
        "request_id": "req-promote",
    }
    assert created["action"] == "user.created"
    assert created["changes"]["email"] == {"before": None, "after": "blair@northfield.example"}
    assert created["request_id"] == "req-create"
    assert "password" not in str(created)


async def test_audit_trail_is_filtered_and_paged(client: httpx.AsyncClient) -> None:
    blair_id = await add_and_promote_blair(client)
    await client.patch(
        "/api/v1/tenant",
        json={"name": "Northfield Supply"},
        headers=bearer(AVERY) | {"If-Match": '"1"'},
    )

    first = await audit(client, resource_type="user", resource_id=blair_id, limit=1)
    second = await audit(
        client,
        resource_type="user",
        resource_id=blair_id,
        limit=1,
        cursor=first.json()["next_cursor"],
    )
    by_action = await audit(client, action="tenant.updated")
    by_actor = await audit(client, actor_id=str(uuid.uuid7()))

    assert [event["action"] for event in first.json()["items"]] == ["user.updated"]
    assert [event["action"] for event in second.json()["items"]] == ["user.created"]
    assert second.json()["next_cursor"] is None
    assert [event["resource_id"] for event in by_action.json()["items"]] == [str(NORTHFIELD.id)]
    assert by_actor.json()["items"] == []


async def test_a_cursor_cannot_continue_another_filter(client: httpx.AsyncClient) -> None:
    await add_and_promote_blair(client)
    first = await audit(client, limit=1)

    response = await audit(client, action="user.created", cursor=first.json()["next_cursor"])

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_cursor"


@pytest.mark.parametrize("role", [Role.SALES_REP, Role.SALES_MANAGER])
async def test_only_admins_read_the_audit_trail(client: httpx.AsyncClient, role: Role) -> None:
    response = await client.get(
        "/api/v1/audit-events", headers=bearer(Principal(NORTHFIELD.id, uuid.uuid7(), role))
    )

    assert response.status_code == 403


async def test_another_tenant_sees_none_of_the_events(client: httpx.AsyncClient) -> None:
    blair_id = await add_and_promote_blair(client)
    outsider = Principal(LARKSPUR.id, uuid.uuid7(), Role.ADMIN)

    response = await client.get(
        "/api/v1/audit-events", params={"resource_id": blair_id}, headers=bearer(outsider)
    )

    assert response.json() == {"items": [], "next_cursor": None}
