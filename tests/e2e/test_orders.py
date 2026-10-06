"""Converting quotes into orders and reading them over HTTP, with in-memory adapters."""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest

from pricewright.api.app import create_app
from pricewright.domain.auth import Principal
from pricewright.domain.catalog import Product, UnitOfMeasure
from pricewright.domain.customers import Customer
from pricewright.domain.money import Money
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeAccessTokens, FakeClock, InMemoryDatabase, fake_services

NORTHFIELD = Tenant.register(
    name="Northfield",
    settings=TenantSettings("USD", Decimal("0.0725"), quote_prefix="NF", order_prefix="NFO"),
)
BOLTS = Product.create(
    tenant_id=NORTHFIELD.id,
    currency="USD",
    sku="FAS-M6-100",
    name="Hex bolt M6 x 100",
    unit=UnitOfMeasure.BOX,
    list_price=Money(Decimal(100), "USD"),
    unit_cost=Money(Decimal(60), "USD"),
)
ACME = Customer.create(
    tenant_id=NORTHFIELD.id, account_number="C-1001", name="Acme", payment_terms_days=45
)
REP = uuid.uuid7()
KEY = str(uuid.uuid4())  # generated: gitleaks flags literal keys


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


def bearer(role: Role = Role.SALES_REP, user_id: uuid.UUID = REP) -> dict[str, str]:
    principal = Principal(NORTHFIELD.id, user_id, role)
    return {"Authorization": f"Bearer {FakeAccessTokens().issue(principal).token}"}


async def accepted(client: httpx.AsyncClient) -> str:
    """A quote with three boxes of bolts, submitted (no approval needed), sent and accepted."""
    quote = await client.post(
        "/api/v1/quotes",
        json={
            "customer_id": str(ACME.id),
            "lines": [{"product_id": str(BOLTS.id), "quantity": "3"}],
        },
        headers=bearer(),
    )
    path = f"/api/v1/quotes/{quote.json()['id']}"
    for version, action in enumerate(("submit", "send", "accept"), start=1):
        moved = await client.post(
            f"{path}/{action}", headers=bearer() | {"If-Match": f'"{version}"'}
        )
        assert moved.status_code == 200, moved.text
    return path


async def convert(
    client: httpx.AsyncClient,
    path: str,
    *,
    version: int = 4,
    key: str | None = KEY,
    body: object = None,
) -> httpx.Response:
    headers = (
        bearer() | {"If-Match": f'"{version}"'} | ({} if key is None else {"Idempotency-Key": key})
    )
    return await client.post(f"{path}/convert", json={} if body is None else body, headers=headers)


async def test_a_rep_converts_an_accepted_quote_into_its_order(client: httpx.AsyncClient) -> None:
    path = await accepted(client)
    quote = (await client.get(path, headers=bearer())).json()

    response = await convert(client, path, body={"customer_reference": "PO-4500123"})

    assert response.status_code == 201, response.text
    order = response.json()
    assert response.headers["location"] == f"/api/v1/orders/{order['id']}"
    assert response.headers["etag"] == '"1"'
    assert "idempotent-replayed" not in response.headers
    assert (order["number"], order["quote_number"], order["quote_id"]) == (
        "NFO-2026-000001",
        "NF-2026-000001",
        quote["id"],
    )
    assert order["customer"] == {
        "id": str(ACME.id),
        "account_number": "C-1001",
        "name": "Acme",
        "tax_id": None,
        "payment_terms_days": 45,
    }
    assert (order["customer_reference"], order["status"], order["cancel_reason"]) == (
        "PO-4500123",
        "open",
        None,
    )
    for field in ("list_subtotal", "net_subtotal", "tax_rate", "tax", "total", "priced_at"):
        assert order[field] == quote[field]
    assert [{k: v for k, v in line.items() if k != "id"} for line in order["lines"]] == [
        {k: v for k, v in line.items() if k not in {"id", "added_by"}} for line in quote["lines"]
    ]
    converted = (await client.get(path, headers=bearer())).json()
    assert (converted["status"], converted["order_id"], converted["allowed_actions"]) == (
        "converted",
        order["id"],
        [],
    )
    read = await client.get(f"/api/v1/orders/{order['id']}", headers=bearer(Role.ADMIN))
    assert (read.status_code, read.headers["etag"], read.json()) == (200, '"1"', order)


async def test_retrying_the_conversion_with_its_key_returns_the_same_order(
    client: httpx.AsyncClient,
) -> None:
    """Brief §7: retrying "convert to order" with the same Idempotency-Key returns the same order,
    never two, even though the retry still carries the quote's old ETag."""
    path = await accepted(client)
    first = await convert(client, path)

    retry = await convert(client, path)

    assert retry.status_code == 201, retry.text
    assert retry.headers["idempotent-replayed"] == "true"
    assert (retry.headers["location"], retry.json()) == (first.headers["location"], first.json())
    quote = (await client.get(path, headers=bearer())).json()
    assert quote["order_id"] == first.json()["id"]


async def test_a_retry_without_a_key_cannot_make_a_second_order(client: httpx.AsyncClient) -> None:
    path = await accepted(client)
    await convert(client, path, key=None)

    stale = await convert(client, path, key=None)
    current = await convert(client, path, key=None, version=5)

    assert (stale.status_code, stale.json()["code"]) == (412, "stale_version")
    assert (current.status_code, current.json()["code"]) == (409, "invalid_transition")


async def test_a_key_reused_for_another_conversion_is_a_422(client: httpx.AsyncClient) -> None:
    path = await accepted(client)
    await convert(client, path)

    response = await convert(client, path, body={"customer_reference": "PO-1"})

    assert (response.status_code, response.json()["code"]) == (422, "idempotency_key_reused")


@pytest.mark.parametrize(
    ("version", "status", "code"),
    [(None, 428, "precondition_required"), (3, 412, "stale_version")],
)
async def test_converting_needs_the_quotes_current_etag(
    client: httpx.AsyncClient, version: int | None, status: int, code: str
) -> None:
    path = await accepted(client)
    headers = bearer() | ({} if version is None else {"If-Match": f'"{version}"'})

    response = await client.post(f"{path}/convert", json={}, headers=headers)

    assert (response.status_code, response.json()["code"]) == (status, code)


async def test_a_quote_that_is_not_accepted_does_not_convert(client: httpx.AsyncClient) -> None:
    quote = await client.post(
        "/api/v1/quotes",
        json={
            "customer_id": str(ACME.id),
            "lines": [{"product_id": str(BOLTS.id), "quantity": "1"}],
        },
        headers=bearer(),
    )

    response = await convert(client, f"/api/v1/quotes/{quote.json()['id']}", version=1)

    assert (response.status_code, response.json()["code"]) == (409, "invalid_transition")


async def test_a_reference_longer_than_35_characters_is_a_422(client: httpx.AsyncClient) -> None:
    path = await accepted(client)

    response = await convert(client, path, body={"customer_reference": "P" * 36})

    assert (response.status_code, response.json()["code"]) == (422, "validation_error")


async def api_key(client: httpx.AsyncClient, *scopes: str) -> dict[str, str]:
    admin = bearer(Role.ADMIN, uuid.uuid7())
    account = await client.post(
        "/api/v1/service-accounts",
        json={"name": f"integration {uuid.uuid7()}", "scopes": list(scopes)},
        headers=admin,
    )
    key = await client.post(
        f"/api/v1/service-accounts/{account.json()['id']}/keys", json={}, headers=admin
    )
    return {"Authorization": f"Bearer {key.json()['key']}"}


async def test_integrations_read_orders_without_margins_and_never_convert(
    client: httpx.AsyncClient,
) -> None:
    path = await accepted(client)
    order = (await convert(client, path)).json()
    integration = await api_key(client, "orders:read", "quotes:read", "quotes:manage")
    refused = await client.post(
        "/api/v1/service-accounts",
        json={"name": "converter", "scopes": ["orders:manage"]},
        headers=bearer(Role.ADMIN, uuid.uuid7()),
    )

    read = await client.get(f"/api/v1/orders/{order['id']}", headers=integration)
    convert_attempt = await client.post(
        f"{path}/convert", json={}, headers=integration | {"If-Match": '"5"'}
    )

    assert read.status_code == 200
    assert "margin" not in read.json()["lines"][0]
    assert read.json()["lines"][0]["below_margin_floor"] is False
    assert (convert_attempt.status_code, convert_attempt.json()["code"]) == (
        403,
        "permission_denied",
    )
    assert refused.status_code == 422  # orders:manage is never granted to a service account


async def test_an_unknown_order_is_a_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/v1/orders/{uuid.uuid7()}", headers=bearer())

    assert (response.status_code, response.json()["code"]) == (404, "not_found")
