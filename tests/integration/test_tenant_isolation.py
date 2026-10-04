"""No API call reaches another tenant's data (brief §4, rule 7; ADR-0006 and ADR-0009).

Runs against the fully wired application and PostgreSQL. Larkspur's admin calls every endpoint that
takes an id with Northfield's ids; every call must be a 404, and Northfield's data must not change.
"""

from dataclasses import dataclass
from decimal import Decimal

import httpx
import pytest

from pricewright.application.onboarding import RegisterTenant, register_tenant
from pricewright.infrastructure.database import create_engine
from pricewright.infrastructure.passwords import Argon2PasswordHasher
from pricewright.main import units_of_work
from tests.integration.data import ADMIN_EMAIL, ADMIN_PASSWORD
from tests.isolation_cases import CASES, IsolationCase

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("session_factory")]

LARKSPUR_EMAIL = "robin@larkspur.example"
LARKSPUR_PASSWORD = "larkspur admin passphrase"


@dataclass(frozen=True)
class World:
    client: httpx.AsyncClient
    northfield_admin: dict[str, str]
    larkspur_admin: dict[str, str]
    northfield_ids: dict[str, str]


async def bearer(client: httpx.AsyncClient, email: str, password: str) -> dict[str, str]:
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.fixture
async def world(northfield_client: httpx.AsyncClient, migrated_database_url: str) -> World:
    """Northfield with a rep, an integration and its key, a category, a product and a customer.

    Larkspur has only its admin.
    """
    engine = create_engine(migrated_database_url)
    await register_tenant(
        RegisterTenant(
            name="Larkspur Tool Co.",
            currency="USD",
            tax_rate=Decimal("0.06"),
            admin_email=LARKSPUR_EMAIL,
            admin_full_name="Robin Admin",
            admin_password=LARKSPUR_PASSWORD,
        ),
        unit_of_work=units_of_work(engine),
        hasher=Argon2PasswordHasher(),
    )
    await engine.dispose()
    client = northfield_client
    northfield = await bearer(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    larkspur = await bearer(client, LARKSPUR_EMAIL, LARKSPUR_PASSWORD)
    rep = await client.post(
        "/api/v1/users",
        json={
            "email": "rep@northfield.example",
            "full_name": "Northfield Rep",
            "role": "sales_rep",
            "password": "northfield rep passphrase",
        },
        headers=northfield,
    )
    account = await client.post(
        "/api/v1/service-accounts",
        json={"name": "ops-copilot", "scopes": ["tenant:read"]},
        headers=northfield,
    )
    key = await client.post(
        f"/api/v1/service-accounts/{account.json()['id']}/keys", json={}, headers=northfield
    )
    category = await client.post(
        "/api/v1/product-categories", json={"name": "Fasteners"}, headers=northfield
    )
    product = await client.post(
        "/api/v1/products",
        json={
            "sku": "FAS-M6-100",
            "name": "Hex bolt M6 x 100",
            "category_id": category.json()["id"],
            "unit": "XBX",
            "list_price": {"amount": "12.50", "currency": "USD"},
            "unit_cost": {"amount": "7.25", "currency": "USD"},
        },
        headers=northfield,
    )
    customer = await client.post(
        "/api/v1/customers",
        json={"account_number": "C-1001", "name": "Acme Industrial", "tax_id": "DE123456789"},
        headers=northfield,
    )
    return World(
        client=client,
        northfield_admin=northfield,
        larkspur_admin=larkspur,
        northfield_ids={
            "user_id": rep.json()["id"],
            "account_id": account.json()["id"],
            "key_id": key.json()["id"],
            "category_id": category.json()["id"],
            "product_id": product.json()["id"],
            "customer_id": customer.json()["id"],
        },
    )


async def call_as_larkspur(world: World, case: IsolationCase) -> httpx.Response:
    return await world.client.request(
        case.method,
        case.path.format(**world.northfield_ids),
        json=case.json,
        headers=world.larkspur_admin | case.headers,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
async def test_another_tenants_id_is_not_found(world: World, case: IsolationCase) -> None:
    response = await call_as_larkspur(world, case)

    assert response.status_code == 404, response.text
    assert response.json()["code"] == "not_found"


async def test_attempts_from_another_tenant_change_nothing(world: World) -> None:
    for case in CASES:
        await call_as_larkspur(world, case)

    ids, admin = world.northfield_ids, world.northfield_admin
    rep = await world.client.get(f"/api/v1/users/{ids['user_id']}", headers=admin)
    keys = await world.client.get(
        f"/api/v1/service-accounts/{ids['account_id']}/keys", headers=admin
    )
    assert (rep.json()["is_active"], rep.json()["version"]) == (True, 1)
    assert [key["id"] for key in keys.json()["items"]] == [ids["key_id"]]
    category = await world.client.get(
        f"/api/v1/product-categories/{ids['category_id']}", headers=admin
    )
    assert (category.json()["name"], category.json()["version"]) == ("Fasteners", 1)
    product = await world.client.get(f"/api/v1/products/{ids['product_id']}", headers=admin)
    assert (product.json()["is_active"], product.json()["version"]) == (True, 1)
    customer = await world.client.get(f"/api/v1/customers/{ids['customer_id']}", headers=admin)
    assert (customer.json()["tier"], customer.json()["version"]) == ("standard", 1)


async def test_lists_only_show_the_callers_tenant(world: World) -> None:
    users = await world.client.get("/api/v1/users", headers=world.larkspur_admin)
    accounts = await world.client.get("/api/v1/service-accounts", headers=world.larkspur_admin)
    tenant = await world.client.get("/api/v1/tenant", headers=world.larkspur_admin)
    categories = await world.client.get("/api/v1/product-categories", headers=world.larkspur_admin)
    products = await world.client.get(
        "/api/v1/products", params={"sku": "FAS-M6-100"}, headers=world.larkspur_admin
    )
    customers = await world.client.get(
        "/api/v1/customers", params={"tax_id": "DE123456789"}, headers=world.larkspur_admin
    )

    assert [user["email"] for user in users.json()["items"]] == [LARKSPUR_EMAIL]
    assert accounts.json()["items"] == []
    assert categories.json()["items"] == []
    assert products.json()["items"] == []
    assert customers.json()["items"] == []
    assert tenant.json()["name"] == "Larkspur Tool Co."
