"""Product categories over HTTP, with in-memory adapters."""

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
PATH = "/api/v1/product-categories"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    database = InMemoryDatabase(tenants={NORTHFIELD.id: NORTHFIELD})
    app = create_app(title="test", services=fake_services(database))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def bearer(role: Role = Role.ADMIN) -> dict[str, str]:
    token = FakeAccessTokens().issue(Principal(NORTHFIELD.id, uuid.uuid7(), role)).token
    return {"Authorization": f"Bearer {token}"}


async def test_an_admin_adds_a_category(client: httpx.AsyncClient) -> None:
    response = await client.post(PATH, json={"name": " Fasteners "}, headers=bearer())

    assert response.status_code == 201
    body = response.json()
    assert (body["name"], body["version"]) == ("Fasteners", 1)
    assert response.headers["Location"] == f"{PATH}/{body['id']}"
    assert response.headers["ETag"] == '"1"'


async def test_a_rep_reads_categories_by_name_page_by_page(client: httpx.AsyncClient) -> None:
    for name in ["Tape", "abrasives", "Gloves"]:
        await client.post(PATH, json={"name": name}, headers=bearer())

    first = await client.get(PATH, params={"limit": 2}, headers=bearer(Role.SALES_REP))
    second = await client.get(
        PATH,
        params={"limit": 2, "cursor": first.json()["next_cursor"]},
        headers=bearer(Role.SALES_REP),
    )

    names = [category["name"] for category in first.json()["items"] + second.json()["items"]]
    assert names == ["abrasives", "Gloves", "Tape"]
    assert second.json()["next_cursor"] is None


async def test_a_rep_cannot_add_a_category(client: httpx.AsyncClient) -> None:
    response = await client.post(PATH, json={"name": "Tape"}, headers=bearer(Role.SALES_REP))

    assert response.status_code == 403


async def test_a_duplicate_name_is_a_conflict(client: httpx.AsyncClient) -> None:
    await client.post(PATH, json={"name": "Fasteners"}, headers=bearer())

    response = await client.post(PATH, json={"name": "fasteners"}, headers=bearer())

    assert response.status_code == 409
    assert response.json()["code"] == "category_name_taken"


async def test_a_category_is_renamed_with_if_match(client: httpx.AsyncClient) -> None:
    created = await client.post(PATH, json={"name": "Fastners"}, headers=bearer())
    url = created.headers["Location"]

    missing = await client.patch(url, json={"name": "Fasteners"}, headers=bearer())
    renamed = await client.patch(
        url, json={"name": "Fasteners"}, headers=bearer() | {"If-Match": '"1"'}
    )
    stale = await client.patch(url, json={"name": "Bolts"}, headers=bearer() | {"If-Match": '"1"'})
    read = await client.get(url, headers=bearer(Role.SALES_REP))

    assert missing.status_code == 428
    assert (renamed.status_code, renamed.headers["ETag"]) == (200, '"2"')
    assert stale.status_code == 412
    assert (read.json()["name"], read.headers["ETag"]) == ("Fasteners", '"2"')


@pytest.mark.parametrize("name", ["", "x" * 101], ids=["empty", "too-long"])
async def test_an_invalid_name_is_a_422(client: httpx.AsyncClient, name: str) -> None:
    response = await client.post(PATH, json={"name": name}, headers=bearer())

    assert response.status_code == 422


async def test_an_unknown_category_is_a_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{PATH}/{uuid.uuid7()}", headers=bearer())

    assert response.status_code == 404
