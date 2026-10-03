"""User management over HTTP, with in-memory adapters."""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest

from pricewright.api.app import create_app
from pricewright.domain.auth import Principal
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import MAX_FAILED_LOGINS, Role, User
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


BLAIR = {
    "email": "blair@northfield.example",
    "full_name": "Blair Manager",
    "role": "sales_manager",
    "password": "blair's initial passphrase",
}


async def test_an_admin_adds_a_user_who_can_then_sign_in(client: httpx.AsyncClient) -> None:
    created = await client.post("/api/v1/users", json=BLAIR, headers=bearer(NORTHFIELD))
    login = await client.post(
        "/api/v1/auth/login", json={"email": BLAIR["email"], "password": BLAIR["password"]}
    )

    assert created.status_code == 201
    assert created.headers["location"] == f"/api/v1/users/{created.json()['id']}"
    assert created.headers["etag"] == '"1"'
    assert created.json()["role"] == "sales_manager"
    assert "password" not in created.text
    assert login.status_code == 200


async def test_adding_an_email_registered_in_any_tenant_is_a_409(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/users", json=BLAIR | {"email": REPS[0].email}, headers=bearer(LARKSPUR)
    )

    assert response.status_code == 409
    assert response.json()["code"] == "email_already_registered"


@pytest.mark.parametrize(
    "changes",
    [{"role": "owner"}, {"email": "not-an-email"}, {"password": "short"}, {"tenant_id": "x"}],
    ids=["role", "email", "password", "extra-field"],
)
async def test_adding_an_invalid_user_is_a_422(
    client: httpx.AsyncClient, changes: dict[str, str]
) -> None:
    response = await client.post("/api/v1/users", json=BLAIR | changes, headers=bearer(NORTHFIELD))

    assert response.status_code == 422


async def test_a_sales_manager_cannot_add_users(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/users", json=BLAIR, headers=bearer(NORTHFIELD, Role.SALES_MANAGER)
    )

    assert response.status_code == 403


async def patch_user(
    client: httpx.AsyncClient,
    user: User,
    body: dict[str, object],
    *,
    if_match: str | None = '"1"',
    tenant: Tenant = NORTHFIELD,
) -> httpx.Response:
    headers = bearer(tenant) | ({"If-Match": if_match} if if_match is not None else {})
    return await client.patch(f"/api/v1/users/{user.id}", json=body, headers=headers)


async def test_reading_a_user_returns_its_etag(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/v1/users/{REPS[1].id}", headers=bearer(NORTHFIELD))

    assert response.headers["etag"] == '"1"'


async def test_an_admin_promotes_a_rep_with_the_current_etag(client: httpx.AsyncClient) -> None:
    response = await patch_user(client, REPS[1], {"role": "sales_manager"})

    assert response.status_code == 200
    assert response.headers["etag"] == '"2"'
    assert (response.json()["role"], response.json()["version"]) == ("sales_manager", 2)


async def test_an_edit_based_on_an_old_etag_is_a_412(client: httpx.AsyncClient) -> None:
    await patch_user(client, REPS[1], {"full_name": "First edit"})

    response = await patch_user(client, REPS[1], {"full_name": "Second edit"})

    assert response.status_code == 412


async def test_editing_a_user_without_if_match_is_a_428(client: httpx.AsyncClient) -> None:
    response = await patch_user(client, REPS[1], {"is_active": False}, if_match=None)

    assert response.status_code == 428


async def test_a_deactivated_user_can_no_longer_sign_in(client: httpx.AsyncClient) -> None:
    created = await client.post("/api/v1/users", json=BLAIR, headers=bearer(NORTHFIELD))
    blair_id = created.json()["id"]

    await client.patch(
        f"/api/v1/users/{blair_id}",
        json={"is_active": False},
        headers=bearer(NORTHFIELD) | {"If-Match": created.headers["etag"]},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": BLAIR["email"], "password": BLAIR["password"]}
    )

    assert login.status_code == 401


async def test_the_last_admin_cannot_be_demoted(client: httpx.AsyncClient) -> None:
    admin = await client.post(
        "/api/v1/users", json=BLAIR | {"role": "admin"}, headers=bearer(NORTHFIELD)
    )

    response = await client.patch(
        f"/api/v1/users/{admin.json()['id']}",
        json={"role": "sales_rep"},
        headers=bearer(NORTHFIELD) | {"If-Match": '"1"'},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "last_admin"


@pytest.mark.parametrize("body", [{}, {"email": "new@northfield.example"}], ids=["empty", "email"])
async def test_invalid_user_edits_are_a_422(
    client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    response = await patch_user(client, REPS[1], body)

    assert response.status_code == 422


async def test_another_tenants_user_cannot_be_edited_or_unlocked(
    client: httpx.AsyncClient,
) -> None:
    edit = await patch_user(client, REPS[1], {"is_active": False}, tenant=LARKSPUR)
    unlock = await client.post(f"/api/v1/users/{REPS[1].id}/unlock", headers=bearer(LARKSPUR))

    assert edit.status_code == unlock.status_code == 404


async def test_an_admin_unlocks_a_locked_out_user(client: httpx.AsyncClient) -> None:
    locked = REPS[2]
    for _ in range(MAX_FAILED_LOGINS):
        await client.post("/api/v1/auth/login", json={"email": locked.email, "password": "x" * 20})
    before = await client.get(f"/api/v1/users/{locked.id}", headers=bearer(NORTHFIELD))

    unlocked = await client.post(f"/api/v1/users/{locked.id}/unlock", headers=bearer(NORTHFIELD))

    assert before.json()["locked"] is True
    assert unlocked.status_code == 200
    assert unlocked.json()["locked"] is False
    assert unlocked.headers["etag"] == f'"{before.json()["version"] + 1}"'
