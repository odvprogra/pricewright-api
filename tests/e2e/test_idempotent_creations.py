"""Idempotency-Key on every creation (ADR-0022), over HTTP with in-memory adapters: a retry gets
the first resource back, the same key with another body is a 422."""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest

from pricewright.api.app import create_app
from pricewright.application.idempotency import replay
from pricewright.domain.auth import Principal
from pricewright.domain.catalog import Product, UnitOfMeasure
from pricewright.domain.customers import Customer
from pricewright.domain.errors import NotFoundError
from pricewright.domain.money import Money
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeAccessTokens, FakeClock, InMemoryDatabase, fake_services

NORTHFIELD = Tenant.register(name="Northfield", settings=TenantSettings("USD", Decimal("0.07")))
BOLTS = Product.create(
    tenant_id=NORTHFIELD.id,
    currency="USD",
    sku="FAS-M6-100",
    name="Hex bolt",
    unit=UnitOfMeasure.BOX,
    list_price=Money(Decimal(100), "USD"),
    unit_cost=Money(Decimal(60), "USD"),
)
ACME = Customer.create(tenant_id=NORTHFIELD.id, account_number="C-1001", name="Acme")
ADMIN = uuid.uuid7()
USD = {"currency": "USD"}

# Each creation, with the body of a first request and a different body for the same key.
type Body = dict[str, object]
CREATIONS: dict[str, tuple[str, Body, Body]] = {
    "customer": (
        "/api/v1/customers",
        {"account_number": "C-2001", "name": "Bolt Co"},
        {"name": "X"},
    ),
    "category": ("/api/v1/product-categories", {"name": "Tape"}, {"name": "Glue"}),
    "product": (
        "/api/v1/products",
        {
            "sku": "TAP-25",
            "name": "Tape 25 mm",
            "unit": "XRO",
            "list_price": {"amount": "3.50"} | USD,
            "unit_cost": {"amount": "1.20"} | USD,
        },
        {"name": "Tape 50 mm"},
    ),
    "pricing rule": (
        "/api/v1/pricing-rules",
        {"kind": "margin_floor", "name": "Keep 20%", "rate": "0.2"},
        {"rate": "0.25"},
    ),
    "user": (
        "/api/v1/users",
        {
            "email": "blair@northfield.example",
            "full_name": "Blair Rep",
            "role": "sales_rep",
            "password": "a long enough passphrase",
        },
        {"role": "sales_manager"},
    ),
    "service account": (
        "/api/v1/service-accounts",
        {"name": "erp-mcp-server", "scopes": ["quotes:read"]},
        {"scopes": ["quotes:read", "quotes:manage"]},
    ),
}


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    database = InMemoryDatabase(
        tenants={NORTHFIELD.id: NORTHFIELD},
        products={BOLTS.id: BOLTS},
        customers={ACME.id: ACME},
    )
    app = create_app(title="test", services=fake_services(database, FakeClock()))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def admin(key: str | None = None) -> dict[str, str]:
    principal = Principal(NORTHFIELD.id, ADMIN, Role.ADMIN)
    token = {"Authorization": f"Bearer {FakeAccessTokens().issue(principal).token}"}
    return token | ({} if key is None else {"Idempotency-Key": key})


@pytest.mark.parametrize("creation", CREATIONS, ids=list(CREATIONS))
async def test_a_retried_creation_returns_the_first_resource(
    client: httpx.AsyncClient, creation: str
) -> None:
    path, body, _ = CREATIONS[creation]
    key = str(uuid.uuid4())

    first = await client.post(path, json=body, headers=admin(key))
    retry = await client.post(path, json=body, headers=admin(key))

    assert (first.status_code, retry.status_code) == (201, 201), retry.text
    assert "idempotent-replayed" not in first.headers
    assert retry.headers["idempotent-replayed"] == "true"
    assert retry.headers["location"] == first.headers["location"]
    assert retry.json() == first.json()


@pytest.mark.parametrize("creation", CREATIONS, ids=list(CREATIONS))
async def test_a_key_reused_for_another_creation_is_a_422(
    client: httpx.AsyncClient, creation: str
) -> None:
    path, body, changes = CREATIONS[creation]
    key = str(uuid.uuid4())
    await client.post(path, json=body, headers=admin(key))

    response = await client.post(path, json=body | changes, headers=admin(key))

    assert (response.status_code, response.json()["code"]) == (422, "idempotency_key_reused")


async def test_a_users_password_is_not_part_of_the_request(client: httpx.AsyncClient) -> None:
    """The fingerprint never hashes a secret, so a retry with another password is the same
    request: the user keeps the first password."""
    path, body, _ = CREATIONS["user"]
    key = str(uuid.uuid4())
    first = await client.post(path, json=body, headers=admin(key))

    retry = await client.post(
        path, json=body | {"password": "another long passphrase"}, headers=admin(key)
    )

    assert retry.headers["idempotent-replayed"] == "true"
    assert retry.json()["id"] == first.json()["id"]


async def test_a_retried_revision_returns_the_same_successor(client: httpx.AsyncClient) -> None:
    quote = await client.post(
        "/api/v1/quotes",
        json={
            "customer_id": str(ACME.id),
            "lines": [{"product_id": str(BOLTS.id), "quantity": "1"}],
        },
        headers=admin(),
    )
    path = f"/api/v1/quotes/{quote.json()['id']}"
    for version, action in enumerate(("submit", "send"), start=1):
        await client.post(f"{path}/{action}", headers=admin() | {"If-Match": f'"{version}"'})
    keyed = admin(str(uuid.uuid4())) | {"If-Match": '"3"'}

    first = await client.post(f"{path}/revise", headers=keyed)
    retry = await client.post(f"{path}/revise", headers=keyed)  # the old ETag, as a retry has

    assert (first.status_code, retry.status_code) == (201, 201), retry.text
    assert retry.headers["idempotent-replayed"] == "true"
    assert retry.json() == first.json()
    assert first.json()["number"].endswith("-R2")


async def test_a_replay_of_something_gone_is_not_found() -> None:
    async def gone() -> None:
        return None

    with pytest.raises(NotFoundError, match="no longer exists"):
        await replay(gone())
