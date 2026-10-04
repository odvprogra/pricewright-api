"""Pricing rules over HTTP, with in-memory adapters."""

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
from tests.fakes import FakeAccessTokens, FakeClock, InMemoryDatabase, fake_services

NORTHFIELD = Tenant.register(name="Northfield", settings=TenantSettings("USD", Decimal(0)))
FASTENERS = ProductCategory.create(tenant_id=NORTHFIELD.id, name="Fasteners")
PATH = "/api/v1/pricing-rules"


def bulk(**changes: object) -> dict[str, object]:
    return {
        "kind": "volume_tier",
        "name": "Fasteners in bulk",
        "category_id": str(FASTENERS.id),
        "brackets": [{"min_quantity": "100", "rate": "0.1"}, {"min_quantity": 10, "rate": 0.05}],
    } | changes


def october(**changes: object) -> dict[str, object]:
    return {
        "kind": "promotion",
        "name": "October",
        "rate": "0.125",
        "valid_from": "2026-10-01T00:00:00-04:00",
        "valid_to": "2026-11-01T00:00:00-04:00",
    } | changes


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    database = InMemoryDatabase(
        tenants={NORTHFIELD.id: NORTHFIELD}, product_categories={FASTENERS.id: FASTENERS}
    )
    app = create_app(title="test", services=fake_services(database, FakeClock()))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def bearer(role: Role = Role.SALES_MANAGER) -> dict[str, str]:
    token = FakeAccessTokens().issue(Principal(NORTHFIELD.id, uuid.uuid7(), role)).token
    return {"Authorization": f"Bearer {token}"}


async def test_a_manager_adds_a_volume_tier_from_now(client: httpx.AsyncClient) -> None:
    response = await client.post(PATH, json=bulk(), headers=bearer())

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["kind"], body["rate"], body["customer_tier"]) == ("volume_tier", None, None)
    assert body["brackets"] == [
        {"min_quantity": "10.000", "rate": "0.0500"},
        {"min_quantity": "100.000", "rate": "0.1000"},
    ]
    assert (body["valid_from"], body["valid_to"]) == ("2026-10-03T12:00:00Z", None)
    assert response.headers["Location"] == f"{PATH}/{body['id']}"
    assert response.headers["ETag"] == '"1"'


async def test_windows_are_kept_in_utc(client: httpx.AsyncClient) -> None:
    response = await client.post(PATH, json=october(), headers=bearer(Role.ADMIN))

    assert (response.json()["valid_from"], response.json()["valid_to"]) == (
        "2026-10-01T04:00:00Z",
        "2026-11-01T04:00:00Z",
    )
    assert response.json()["rate"] == "0.1250"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (october(valid_to=None), "invalid_pricing_rule"),
        (bulk(rate="0.1"), "invalid_pricing_rule"),
        (october(rate="0.12345"), "invalid_pricing_rule"),
        ({"kind": "customer_tier", "name": "Gold", "rate": "0.05"}, "invalid_pricing_rule"),
        (bulk(category_id=str(uuid.uuid7())), "unknown_category"),
        (bulk(category_id=None, product_id=str(uuid.uuid7())), "unknown_product"),
        (october(valid_to="2026-11-01T00:00:00"), "validation_error"),
        (october(rate="NaN"), "validation_error"),
        (october(kind="exclusive_promotion"), "validation_error"),
    ],
    ids=[
        "promotion-without-end",
        "rate-on-volume-tier",
        "five-places",
        "tier-discount-without-tier",
        "unknown-category",
        "unknown-product",
        "naive-time",
        "nan",
        "unknown-kind",
    ],
)
async def test_invalid_rules_are_a_422(
    client: httpx.AsyncClient, body: dict[str, object], code: str
) -> None:
    response = await client.post(PATH, json=body, headers=bearer())

    assert response.status_code == 422
    assert response.json()["code"] == code


async def test_reps_read_the_rules_but_cannot_change_them(client: httpx.AsyncClient) -> None:
    created = await client.post(PATH, json=october(), headers=bearer())

    read = await client.get(created.headers["Location"], headers=bearer(Role.SALES_REP))
    add = await client.post(PATH, json=october(), headers=bearer(Role.SALES_REP))
    edit = await client.patch(
        created.headers["Location"],
        json={"is_active": False},
        headers=bearer(Role.SALES_REP) | {"If-Match": '"1"'},
    )

    assert read.status_code == 200
    assert (add.status_code, edit.status_code) == (403, 403)
    assert add.json()["code"] == "permission_denied"


async def test_integrations_may_read_rules_but_never_manage_them(
    client: httpx.AsyncClient,
) -> None:
    admin = bearer(Role.ADMIN)
    await client.post(PATH, json=october(), headers=admin)
    account = await client.post(
        "/api/v1/service-accounts",
        json={"name": "erp-mcp-server", "scopes": ["pricing:read"]},
        headers=admin,
    )
    key = await client.post(
        f"/api/v1/service-accounts/{account.json()['id']}/keys", json={}, headers=admin
    )
    refused = await client.post(
        "/api/v1/service-accounts",
        json={"name": "rogue", "scopes": ["pricing:manage"]},
        headers=admin,
    )

    listed = await client.get(PATH, headers={"Authorization": f"Bearer {key.json()['key']}"})

    assert [rule["name"] for rule in listed.json()["items"]] == ["October"]
    assert refused.status_code == 422


async def test_rules_are_filtered_sorted_and_paged(client: httpx.AsyncClient) -> None:
    for name in ["Weekend", "autumn", "Clearance"]:
        await client.post(PATH, json=october(name=name), headers=bearer())
    await client.post(PATH, json=bulk(), headers=bearer())
    params: dict[str, str | int] = {"kind": "promotion", "sort": "-name", "limit": 2}

    first = await client.get(PATH, params=params, headers=bearer())
    second = await client.get(
        PATH, params=params | {"cursor": first.json()["next_cursor"]}, headers=bearer()
    )
    by_category = await client.get(
        PATH, params={"category_id": str(FASTENERS.id)}, headers=bearer()
    )
    other_query = await client.get(
        PATH, params={"cursor": first.json()["next_cursor"]}, headers=bearer()
    )

    assert [r["name"] for r in first.json()["items"] + second.json()["items"]] == [
        "Weekend",
        "Clearance",
        "autumn",
    ]
    assert second.json()["next_cursor"] is None
    assert [r["name"] for r in by_category.json()["items"]] == ["Fasteners in bulk"]
    assert other_query.status_code == 422


async def test_a_rule_is_edited_end_dated_and_deactivated_with_if_match(
    client: httpx.AsyncClient,
) -> None:
    created = await client.post(PATH, json=bulk(), headers=bearer())
    url = created.headers["Location"]

    missing = await client.patch(url, json={"is_active": False}, headers=bearer())
    edited = await client.patch(
        url,
        json={
            "brackets": [{"min_quantity": "50", "rate": "0.08"}],
            "valid_to": "2026-12-31T00:00:00Z",
        },
        headers=bearer() | {"If-Match": '"1"'},
    )
    stale = await client.patch(
        url, json={"is_active": False}, headers=bearer() | {"If-Match": '"1"'}
    )
    reopened = await client.patch(
        url, json={"valid_to": None, "is_active": False}, headers=bearer() | {"If-Match": '"2"'}
    )

    assert missing.status_code == 428
    assert edited.json()["brackets"] == [{"min_quantity": "50.000", "rate": "0.0800"}]
    assert stale.status_code == 412
    assert (reopened.json()["valid_to"], reopened.json()["is_active"]) == (None, False)
    assert reopened.headers["ETag"] == '"3"'


@pytest.mark.parametrize(
    "body",
    [{}, {"kind": "promotion"}, {"category_id": str(uuid.uuid7())}],
    ids=["nothing", "kind", "scope"],
)
async def test_a_patch_changes_terms_never_identity(
    client: httpx.AsyncClient, body: dict[str, str]
) -> None:
    created = await client.post(PATH, json=bulk(), headers=bearer())

    response = await client.patch(
        created.headers["Location"], json=body, headers=bearer() | {"If-Match": '"1"'}
    )

    assert response.status_code == 422


async def test_an_unknown_rule_is_a_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{PATH}/{uuid.uuid7()}", headers=bearer())

    assert response.status_code == 404
