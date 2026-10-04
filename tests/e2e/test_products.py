"""The product catalog over HTTP, with in-memory adapters."""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest

from pricewright.api.app import create_app
from pricewright.domain.auth import Principal
from pricewright.domain.catalog import ProductCategory
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeAccessTokens, InMemoryDatabase, fake_services

NORTHFIELD = Tenant.register(name="Northfield", settings=TenantSettings("USD", Decimal(0)))
FASTENERS = ProductCategory.create(tenant_id=NORTHFIELD.id, name="Fasteners")
PATH = "/api/v1/products"


def bolts(**changes: object) -> dict[str, object]:
    return {
        "sku": "FAS-M6-100",
        "name": "Hex bolt M6 x 100",
        "category_id": str(FASTENERS.id),
        "unit": "XBX",
        "list_price": {"amount": "12.5", "currency": "USD"},
        "unit_cost": {"amount": "7.25", "currency": "USD"},
    } | changes


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    database = InMemoryDatabase(
        tenants={NORTHFIELD.id: NORTHFIELD}, product_categories={FASTENERS.id: FASTENERS}
    )
    app = create_app(title="test", services=fake_services(database))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def bearer(role: Role = Role.ADMIN) -> dict[str, str]:
    token = FakeAccessTokens().issue(Principal(NORTHFIELD.id, uuid.uuid7(), role)).token
    return {"Authorization": f"Bearer {token}"}


async def test_an_admin_adds_a_product_and_money_comes_back_exact(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(PATH, json=bolts(), headers=bearer())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["list_price"] == {"amount": "12.5000", "currency": "USD"}
    assert body["unit_cost"] == {"amount": "7.2500", "currency": "USD"}
    assert (body["sku"], body["unit"], body["is_active"], body["version"]) == (
        "FAS-M6-100",
        "XBX",
        True,
        1,
    )
    assert response.headers["Location"] == f"{PATH}/{body['id']}"
    assert response.headers["ETag"] == '"1"'


@pytest.mark.parametrize(
    "amount",
    [12.5, "12.34567", "1e3", "12,50", ""],
    ids=["json-number", "five-places", "exponent", "comma", "empty"],
)
async def test_amounts_are_decimal_strings_with_up_to_4_places(
    client: httpx.AsyncClient, amount: object
) -> None:
    response = await client.post(
        PATH, json=bolts(list_price={"amount": amount, "currency": "USD"}), headers=bearer()
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"list_price": {"amount": "12.50", "currency": "EUR"}}, "invalid_catalog"),
        ({"unit_cost": {"amount": "-1", "currency": "USD"}}, "invalid_catalog"),
        ({"category_id": str(uuid.uuid7())}, "unknown_category"),
        ({"sku": "has space"}, "invalid_catalog"),
        ({"unit": "BOX"}, "validation_error"),
    ],
    ids=["other-currency", "negative", "unknown-category", "bad-sku", "unknown-unit"],
)
async def test_invalid_products_are_a_422(
    client: httpx.AsyncClient, changes: dict[str, object], code: str
) -> None:
    response = await client.post(PATH, json=bolts(**changes), headers=bearer())

    assert response.status_code == 422
    assert response.json()["code"] == code


async def test_a_duplicate_sku_is_a_conflict(client: httpx.AsyncClient) -> None:
    await client.post(PATH, json=bolts(), headers=bearer())

    response = await client.post(PATH, json=bolts(sku="fas-m6-100"), headers=bearer())

    assert response.status_code == 409
    assert response.json()["code"] == "sku_taken"


async def test_the_catalog_is_searched_sorted_and_paged(client: httpx.AsyncClient) -> None:
    for sku, name in [("TAPE-48", "Packing tape"), ("BOLT-M6", "Hex bolt"), ("NUT-M6", "Hex nut")]:
        await client.post(PATH, json=bolts(sku=sku, name=name), headers=bearer())
    params: dict[str, str | int] = {"q": "hex", "sort": "-sku", "limit": 1}

    first = await client.get(PATH, params=params, headers=bearer(Role.SALES_REP))
    second = await client.get(
        PATH, params=params | {"cursor": first.json()["next_cursor"]}, headers=bearer()
    )
    by_sku = await client.get(PATH, params={"sku": "tape-48"}, headers=bearer())
    with_other_filter = await client.get(
        PATH, params={"q": "tape", "cursor": first.json()["next_cursor"]}, headers=bearer()
    )

    assert [p["sku"] for p in first.json()["items"] + second.json()["items"]] == [
        "NUT-M6",
        "BOLT-M6",
    ]
    assert second.json()["next_cursor"] is None
    assert [p["name"] for p in by_sku.json()["items"]] == ["Packing tape"]
    assert with_other_filter.status_code == 422


async def test_a_product_is_edited_archived_and_restored_with_if_match(
    client: httpx.AsyncClient,
) -> None:
    created = await client.post(PATH, json=bolts(), headers=bearer())
    url = created.headers["Location"]

    missing = await client.patch(url, json={"is_active": False}, headers=bearer())
    archived = await client.patch(
        url, json={"is_active": False, "category_id": None}, headers=bearer() | {"If-Match": '"1"'}
    )
    stale = await client.patch(
        url, json={"is_active": True}, headers=bearer() | {"If-Match": '"1"'}
    )
    active = await client.get(PATH, params={"active": True}, headers=bearer())
    restored = await client.patch(
        url,
        json={"is_active": True, "list_price": {"amount": "13.0001", "currency": "USD"}},
        headers=bearer() | {"If-Match": '"2"'},
    )

    assert missing.status_code == 428
    assert (archived.json()["is_active"], archived.json()["category_id"]) == (False, None)
    assert stale.status_code == 412
    assert active.json()["items"] == []
    assert restored.json()["list_price"]["amount"] == "13.0001"
    assert restored.headers["ETag"] == '"3"'


@pytest.mark.parametrize("body", [{"sku": "NEW"}, {}], ids=["sku", "nothing"])
async def test_a_patch_must_change_something_other_than_the_sku(
    client: httpx.AsyncClient, body: dict[str, str]
) -> None:
    created = await client.post(PATH, json=bolts(), headers=bearer())

    response = await client.patch(
        created.headers["Location"], json=body, headers=bearer() | {"If-Match": '"1"'}
    )

    assert response.status_code == 422


@pytest.mark.parametrize("role", [Role.SALES_REP, Role.SALES_MANAGER])
async def test_only_admins_change_the_catalog(client: httpx.AsyncClient, role: Role) -> None:
    response = await client.post(PATH, json=bolts(), headers=bearer(role))

    assert response.status_code == 403
