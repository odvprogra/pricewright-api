"""User management over HTTP, with in-memory adapters."""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest

from pricewright.api.app import create_app
from pricewright.domain.auth import Principal
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role, User
from tests.fakes import FakeAccessTokens, InMemoryDatabase, fake_services

NORTHFIELD = Tenant.register(name="Northfield", settings=TenantSettings("USD", Decimal(0)))
LARKSPUR = Tenant.register(name="Larkspur", settings=TenantSettings("USD", Decimal(0)))
REPS = [
    User.create(
        tenant_id=NORTHFIELD.id,
        email=f"rep{number}@northfield.example",
        full_name=f"Rep {number}",
        role=Role.SALES_REP,
        password_hash="hash",
    )
    for number in range(3)
]


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    database = InMemoryDatabase(
        tenants={NORTHFIELD.id: NORTHFIELD, LARKSPUR.id: LARKSPUR},
        users={user.id: user for user in REPS},
    )
    app = create_app(title="test", services=fake_services(database))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def bearer(tenant: Tenant, role: Role = Role.ADMIN) -> dict[str, str]:
    token = FakeAccessTokens().issue(Principal(tenant.id, uuid.uuid7(), role)).token
    return {"Authorization": f"Bearer {token}"}


async def test_users_are_listed_page_by_page(client: httpx.AsyncClient) -> None:
    first = await client.get("/api/v1/users", params={"limit": 2}, headers=bearer(NORTHFIELD))
    second = await client.get(
        "/api/v1/users",
        params={"limit": 2, "cursor": first.json()["next_cursor"]},
        headers=bearer(NORTHFIELD),
    )

    emails = [user["email"] for user in first.json()["items"] + second.json()["items"]]
    assert emails == [user.email for user in REPS]
    assert second.json()["next_cursor"] is None


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"cursor": "not-a-cursor!"}],
    ids=["zero", "too-big", "cursor"],
)
async def test_invalid_paging_parameters_are_a_422(
    client: httpx.AsyncClient, params: dict[str, int | str]
) -> None:
    response = await client.get("/api/v1/users", params=params, headers=bearer(NORTHFIELD))

    assert response.status_code == 422


async def test_a_user_is_read_by_id(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/v1/users/{REPS[0].id}", headers=bearer(NORTHFIELD))

    assert response.status_code == 200
    assert response.json()["email"] == REPS[0].email


async def test_another_tenants_user_is_a_404_like_one_that_does_not_exist(
    client: httpx.AsyncClient,
) -> None:
    foreign = await client.get(f"/api/v1/users/{REPS[0].id}", headers=bearer(LARKSPUR))
    missing = await client.get(f"/api/v1/users/{uuid.uuid7()}", headers=bearer(LARKSPUR))

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() | {"instance": None} == missing.json() | {"instance": None}


async def test_a_sales_rep_cannot_list_users(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/users", headers=bearer(NORTHFIELD, Role.SALES_REP))

    assert response.status_code == 403
