"""Customers over HTTP, with in-memory adapters."""

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
PATH = "/api/v1/customers"


def acme(**changes: object) -> dict[str, object]:
    return {"account_number": "C-1001", "name": "Acme Industrial"} | changes


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    database = InMemoryDatabase(tenants={NORTHFIELD.id: NORTHFIELD})
    app = create_app(title="test", services=fake_services(database))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def bearer(role: Role = Role.SALES_REP) -> dict[str, str]:
    token = FakeAccessTokens().issue(Principal(NORTHFIELD.id, uuid.uuid7(), role)).token
    return {"Authorization": f"Bearer {token}"}


async def test_a_rep_adds_a_customer_with_default_terms(client: httpx.AsyncClient) -> None:
    response = await client.post(PATH, json=acme(tax_id="de 123.456-789"), headers=bearer())

    assert response.status_code == 201, response.text
    body = response.json()
    assert {key: body[key] for key in ("tax_id", "tier", "payment_terms_days", "version")} == {
        "tax_id": "DE123456789",
        "tier": "standard",
        "payment_terms_days": 30,
        "version": 1,
    }
    assert response.headers["Location"] == f"{PATH}/{body['id']}"
    assert response.headers["ETag"] == '"1"'


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"payment_terms_days": 366}, "validation_error"),
        ({"payment_terms_days": -1}, "validation_error"),
        ({"tier": "platinum"}, "validation_error"),
        ({"tax_id": "12#34"}, "invalid_customer"),
        ({"account_number": "has space"}, "invalid_customer"),
        ({"name": " "}, "invalid_customer"),
    ],
    ids=["terms-too-long", "terms-negative", "unknown-tier", "bad-tax-id", "bad-number", "blank"],
)
async def test_invalid_customers_are_a_422(
    client: httpx.AsyncClient, changes: dict[str, object], code: str
) -> None:
    response = await client.post(PATH, json=acme(**changes), headers=bearer())

    assert response.status_code == 422
    assert response.json()["code"] == code


async def test_a_duplicate_account_number_is_a_conflict(client: httpx.AsyncClient) -> None:
    await client.post(PATH, json=acme(), headers=bearer())

    response = await client.post(PATH, json=acme(account_number="c-1001"), headers=bearer())

    assert response.status_code == 409
    assert response.json()["code"] == "account_number_taken"


async def test_customers_are_searched_sorted_and_paged(client: httpx.AsyncClient) -> None:
    for number, name, tax_id in [
        ("C-3", "Zenith Tools", None),
        ("C-1", "Acme Industrial", "DE 1"),
        ("C-2", "Acme Labs", "DE-1"),
    ]:
        await client.post(
            PATH, json=acme(account_number=number, name=name, tax_id=tax_id), headers=bearer()
        )
    params: dict[str, str | int] = {"q": "acme", "sort": "-account_number", "limit": 1}

    first = await client.get(PATH, params=params, headers=bearer())
    second = await client.get(
        PATH, params=params | {"cursor": first.json()["next_cursor"]}, headers=bearer()
    )
    by_tax_id = await client.get(PATH, params={"tax_id": "de.1"}, headers=bearer())
    bad_tax_id = await client.get(PATH, params={"tax_id": "#"}, headers=bearer())

    assert [c["account_number"] for c in first.json()["items"] + second.json()["items"]] == [
        "C-2",
        "C-1",
    ]
    assert second.json()["next_cursor"] is None
    assert [c["account_number"] for c in by_tax_id.json()["items"]] == ["C-1", "C-2"]
    assert bad_tax_id.status_code == 422


async def test_a_customer_is_edited_archived_and_restored_with_if_match(
    client: httpx.AsyncClient,
) -> None:
    created = await client.post(PATH, json=acme(tax_id="DE1"), headers=bearer())
    url = created.headers["Location"]

    missing = await client.patch(url, json={"tier": "gold"}, headers=bearer())
    edited = await client.patch(
        url,
        json={"tier": "gold", "payment_terms_days": 0, "tax_id": None},
        headers=bearer() | {"If-Match": '"1"'},
    )
    stale = await client.patch(
        url, json={"is_active": False}, headers=bearer() | {"If-Match": '"1"'}
    )
    archived = await client.patch(
        url, json={"is_active": False}, headers=bearer() | {"If-Match": '"2"'}
    )
    read = await client.get(url, headers=bearer())

    assert missing.status_code == 428
    assert (edited.json()["tier"], edited.json()["payment_terms_days"]) == ("gold", 0)
    assert edited.json()["tax_id"] is None
    assert stale.status_code == 412
    assert archived.json()["is_active"] is False
    assert (read.json()["version"], read.headers["ETag"]) == (3, '"3"')


@pytest.mark.parametrize("body", [{"account_number": "C-9"}, {}], ids=["number", "nothing"])
async def test_a_patch_must_change_something_other_than_the_account_number(
    client: httpx.AsyncClient, body: dict[str, str]
) -> None:
    created = await client.post(PATH, json=acme(), headers=bearer())

    response = await client.patch(
        created.headers["Location"], json=body, headers=bearer() | {"If-Match": '"1"'}
    )

    assert response.status_code == 422


async def test_an_unknown_customer_is_a_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{PATH}/{uuid.uuid7()}", headers=bearer(Role.SALES_MANAGER))

    assert response.status_code == 404
