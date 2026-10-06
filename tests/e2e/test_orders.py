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


async def test_orders_are_listed_newest_first_filtered_and_paged(
    client: httpx.AsyncClient,
) -> None:
    first = (await convert(client, await accepted(client), key=None)).json()
    second = (await convert(client, await accepted(client), key=None)).json()
    manager = bearer(Role.SALES_MANAGER, uuid.uuid7())

    listed = await client.get("/api/v1/orders", headers=manager)
    oldest = await client.get("/api/v1/orders", params={"sort": "created_at"}, headers=manager)
    by_number = await client.get(
        "/api/v1/orders", params={"number": "NFO-2026-000001"}, headers=manager
    )
    by_customer = await client.get(
        "/api/v1/orders",
        params={"customer_id": str(ACME.id), "status": "open", "created_by": str(REP)},
        headers=manager,
    )
    page = await client.get("/api/v1/orders", params={"limit": 1}, headers=manager)
    rest = await client.get(
        "/api/v1/orders", params={"limit": 1, "cursor": page.json()["next_cursor"]}, headers=manager
    )

    assert [order["id"] for order in listed.json()["items"]] == [second["id"], first["id"]]
    assert [order["id"] for order in oldest.json()["items"]] == [first["id"], second["id"]]
    assert [order["id"] for order in by_number.json()["items"]] == [first["id"]]
    assert len(by_customer.json()["items"]) == 2
    assert [order["id"] for order in page.json()["items"] + rest.json()["items"]] == [
        second["id"],
        first["id"],
    ]
    assert rest.json()["next_cursor"] is None
    assert listed.json()["items"][0] == {
        "id": second["id"],
        "number": "NFO-2026-000002",
        "quote_id": second["quote_id"],
        "quote_number": "NF-2026-000002",
        "customer_id": str(ACME.id),
        "customer_name": "Acme",
        "customer_reference": None,
        "status": "open",
        "total": second["total"],
        "created_by": {"type": "user", "id": str(REP)},
        "created_at": second["created_at"],
        "status_changed_at": second["status_changed_at"],
        "version": 1,
    }


async def test_an_order_list_cursor_works_only_with_its_own_query(
    client: httpx.AsyncClient,
) -> None:
    for _ in range(2):
        await convert(client, await accepted(client), key=None)
    page = await client.get("/api/v1/orders", params={"limit": 1}, headers=bearer())

    response = await client.get(
        "/api/v1/orders",
        params={"limit": 1, "status": "open", "cursor": page.json()["next_cursor"]},
        headers=bearer(),
    )

    assert (response.status_code, response.json()["code"]) == (422, "invalid_cursor")


async def test_listing_orders_needs_orders_read(client: httpx.AsyncClient) -> None:
    await convert(client, await accepted(client), key=None)
    reader = await api_key(client, "orders:read")
    stranger = await api_key(client, "quotes:read")

    allowed = await client.get("/api/v1/orders", headers=reader)
    refused = await client.get("/api/v1/orders", headers=stranger)

    assert len(allowed.json()["items"]) == 1
    assert (refused.status_code, refused.json()["code"]) == (403, "permission_denied")


async def cancel_order(
    client: httpx.AsyncClient, order_id: str, headers: dict[str, str]
) -> httpx.Response:
    return await client.post(
        f"/api/v1/orders/{order_id}/cancel", json={"reason": "Entered twice"}, headers=headers
    )


async def test_a_rep_cancels_an_open_order_with_a_reason(client: httpx.AsyncClient) -> None:
    path = await accepted(client)
    order = (await convert(client, path)).json()

    response = await cancel_order(client, order["id"], bearer() | {"If-Match": '"1"'})
    again = await cancel_order(client, order["id"], bearer() | {"If-Match": '"2"'})
    listed = await client.get("/api/v1/orders", params={"status": "cancelled"}, headers=bearer())
    quote = (await client.get(path, headers=bearer())).json()

    assert (response.status_code, response.headers["etag"]) == (200, '"2"')
    assert (response.json()["status"], response.json()["cancel_reason"]) == (
        "cancelled",
        "Entered twice",
    )
    assert (again.status_code, again.json()["code"]) == (409, "invalid_transition")
    assert [item["id"] for item in listed.json()["items"]] == [order["id"]]
    assert quote["status"] == "converted"


@pytest.mark.parametrize(
    ("if_match", "status", "code"),
    [(None, 428, "precondition_required"), ('"7"', 412, "stale_version")],
)
async def test_cancelling_an_order_needs_its_current_etag(
    client: httpx.AsyncClient, if_match: str | None, status: int, code: str
) -> None:
    order = (await convert(client, await accepted(client))).json()
    headers = bearer() | ({} if if_match is None else {"If-Match": if_match})

    response = await cancel_order(client, order["id"], headers)

    assert (response.status_code, response.json()["code"]) == (status, code)


async def test_integrations_never_cancel_orders(client: httpx.AsyncClient) -> None:
    order = (await convert(client, await accepted(client))).json()
    integration = await api_key(client, "orders:read")

    response = await cancel_order(client, order["id"], integration | {"If-Match": '"1"'})
    empty = await client.post(
        f"/api/v1/orders/{order['id']}/cancel",
        json={"reason": ""},
        headers=bearer() | {"If-Match": '"1"'},
    )

    assert (response.status_code, response.json()["code"]) == (403, "permission_denied")
    assert (empty.status_code, empty.json()["code"]) == (422, "validation_error")
