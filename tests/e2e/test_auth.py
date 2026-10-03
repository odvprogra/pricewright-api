"""Sign-in and the signed-in user over HTTP, with in-memory adapters."""

from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest

from pricewright.api.app import create_app
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role, User
from tests.fakes import FakeAccessTokens, FakePasswordHasher, InMemoryDatabase, fake_services

PASSWORD = "avery's long passphrase"
PROBLEM_JSON = "application/problem+json"


@pytest.fixture
def avery() -> User:
    tenant = Tenant.register(name="Northfield", settings=TenantSettings("USD", Decimal(0)))
    return User.create(
        tenant_id=tenant.id,
        email="avery@northfield.example",
        full_name="Avery Admin",
        role=Role.ADMIN,
        password_hash=FakePasswordHasher.PREFIX + PASSWORD,
    )


@pytest.fixture
def database(avery: User) -> InMemoryDatabase:
    return InMemoryDatabase(users={avery.id: avery})


@pytest.fixture
async def client(database: InMemoryDatabase) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(title="test", services=fake_services(database))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def log_in(client: httpx.AsyncClient, password: str = PASSWORD) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login", json={"email": "avery@northfield.example", "password": password}
    )


async def test_login_returns_a_bearer_token_that_is_never_cached(
    client: httpx.AsyncClient,
) -> None:
    response = await log_in(client)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == FakeAccessTokens.EXPIRES_IN
    assert body["access_token"].startswith("token:")
    assert response.headers["cache-control"] == "no-store"


async def test_login_with_a_wrong_password_is_a_401_problem(client: httpx.AsyncClient) -> None:
    response = await log_in(client, password="not avery's passphrase")

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {
        "type": "about:blank",
        "title": "Unauthorized",
        "status": 401,
        "detail": "invalid email or password",
        "instance": "/api/v1/auth/login",
        "code": "authentication_failed",
    }


async def test_login_never_echoes_the_password_in_validation_errors(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": "avery@northfield.example", "password": "x" * 2000}
    )

    assert response.status_code == 422
    assert "xxxx" not in response.text


async def test_me_returns_the_signed_in_user(client: httpx.AsyncClient, avery: User) -> None:
    token = (await log_in(client)).json()["access_token"]

    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {
        "id": str(avery.id),
        "tenant_id": str(avery.tenant_id),
        "email": "avery@northfield.example",
        "full_name": "Avery Admin",
        "role": "admin",
    }


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer forged"}, {"Authorization": "Basic YXZlcnk6cHc="}],
    ids=["missing", "invalid", "not-bearer"],
)
async def test_me_without_a_valid_token_is_a_401_problem(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/me", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["code"] == "authentication_failed"


async def test_me_after_the_account_is_deactivated_is_a_401(
    client: httpx.AsyncClient, database: InMemoryDatabase, avery: User
) -> None:
    token = (await log_in(client)).json()["access_token"]
    database.users[avery.id].is_active = False

    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
