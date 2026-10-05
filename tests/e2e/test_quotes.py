"""Quotes over HTTP, with in-memory adapters."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from pricewright.api.app import create_app
from pricewright.domain.auth import Principal
from pricewright.domain.catalog import Product, UnitOfMeasure
from pricewright.domain.customers import Customer, CustomerTier
from pricewright.domain.money import Money
from pricewright.domain.pricing_rules import Bracket, PricingRule, RuleKind
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeAccessTokens, FakeClock, InMemoryDatabase, fake_services

NORTHFIELD = Tenant.register(
    name="Northfield", settings=TenantSettings("USD", Decimal("0.0725"), quote_prefix="NF")
)
SINCE = datetime(2026, 10, 1, tzinfo=UTC)
BOLTS = Product.create(
    tenant_id=NORTHFIELD.id,
    currency="USD",
    sku="FAS-M6-100",
    name="Hex bolt M6 x 100",
    unit=UnitOfMeasure.BOX,
    list_price=Money(Decimal(100), "USD"),
    unit_cost=Money(Decimal(80), "USD"),
)
ACME = Customer.create(
    tenant_id=NORTHFIELD.id, account_number="C-1001", name="Acme", tier=CustomerTier.GOLD
)
VOLUME = PricingRule.create(
    tenant_id=NORTHFIELD.id,
    kind=RuleKind.VOLUME_TIER,
    name="Bulk",
    valid_from=SINCE,
    brackets=[Bracket(Decimal(10), Decimal("0.1"))],
)
FLOOR = PricingRule.create(
    tenant_id=NORTHFIELD.id,
    kind=RuleKind.MARGIN_FLOOR,
    name="Tenant floor",
    rate=Decimal("0.15"),
    valid_from=SINCE,
)
PATH = "/api/v1/quotes"


@pytest.fixture
def database() -> InMemoryDatabase:
    return InMemoryDatabase(
        tenants={NORTHFIELD.id: NORTHFIELD},
        products={BOLTS.id: BOLTS},
        customers={ACME.id: ACME},
        pricing_rules={rule.id: rule for rule in (VOLUME, FLOOR)},
    )


@pytest.fixture
async def client(database: InMemoryDatabase) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(title="test", services=fake_services(database, FakeClock()))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def bearer(role: Role = Role.SALES_REP, user_id: uuid.UUID | None = None) -> dict[str, str]:
    principal = Principal(NORTHFIELD.id, user_id or uuid.uuid7(), role)
    return {"Authorization": f"Bearer {FakeAccessTokens().issue(principal).token}"}


async def api_key(client: httpx.AsyncClient, *scopes: str) -> dict[str, str]:
    admin = bearer(Role.ADMIN)
    account = await client.post(
        "/api/v1/service-accounts",
        json={"name": f"integration {uuid.uuid7()}", "scopes": list(scopes)},
        headers=admin,
    )
    key = await client.post(
        f"/api/v1/service-accounts/{account.json()['id']}/keys", json={}, headers=admin
    )
    return {"Authorization": f"Bearer {key.json()['key']}"}


def new_quote(**changes: object) -> dict[str, object]:
    return {
        "customer_id": str(ACME.id),
        "lines": [{"product_id": str(BOLTS.id), "quantity": "10"}],
    } | changes


def usd(amount: str) -> dict[str, str]:
    return {"amount": amount, "currency": "USD"}


async def test_a_rep_starts_a_draft_priced_line_by_line(client: httpx.AsyncClient) -> None:
    rep_id = uuid.uuid7()

    response = await client.post(
        PATH, json=new_quote(notes="Rush order"), headers=bearer(user_id=rep_id)
    )

    assert response.status_code == 201
    body = response.json()
    assert response.headers["location"] == f"{PATH}/{body['id']}"
    assert response.headers["etag"] == '"1"'
    assert body | {"id": None, "lines": None} == {
        "id": None,
        "number": "NF-2026-000001",
        "revision": 1,
        "customer_id": str(ACME.id),
        "status": "draft",
        "valid_until": "2026-11-02",
        "notes": "Rush order",
        "lines": None,
        "list_subtotal": usd("1000.0000"),
        "net_subtotal": usd("900.0000"),
        "tax_rate": "0.0725",
        "tax": usd("65.2500"),
        "total": usd("965.2500"),
        "discount": "0.1000",
        "approval_threshold": "0.1500",
        "requires_approval": True,  # the line keeps 11% against a 15% floor
        "approval_reasons": ["line_below_margin_floor"],
        "priced_at": "2026-10-03T12:00:00Z",
        "allowed_actions": ["cancel", "submit"],
        "created_by": {"type": "user", "id": str(rep_id)},
        "created_at": "2026-10-03T12:00:00Z",
        "status_changed_at": "2026-10-03T12:00:00Z",
        "submitted_by": None,
        "submitted_at": None,
        "cancel_reason": None,
        "supersedes_id": None,
        "superseded_by_id": None,
        "approvals": [],
        "version": 1,
    }
    [line] = body["lines"]
    assert line | {"id": None, "steps": None} == {
        "id": None,
        "product_id": str(BOLTS.id),
        "sku": "FAS-M6-100",
        "product_name": "Hex bolt M6 x 100",
        "unit": "XBX",
        "quantity": "10.000",
        "list_unit_price": usd("100.0000"),
        "steps": None,
        "net_unit_price": usd("90.0000"),
        "list_total": usd("1000.0000"),
        "net_total": usd("900.0000"),
        "margin": {"amount": usd("100.0000"), "rate": "0.1111"},
        "margin_floor": {"rule_id": str(FLOOR.id), "label": "Tenant floor", "rate": "0.1500"},
        "below_margin_floor": True,
        "override": None,
        "added_by": {"type": "user", "id": str(rep_id)},
    }
    assert [step["stage"] for step in line["steps"]] == ["volume_tier"]


async def test_an_integration_drafts_quotes_without_seeing_margins(
    client: httpx.AsyncClient,
) -> None:
    key = await api_key(client, "quotes:read", "quotes:manage")

    created = await client.post(PATH, json=new_quote(), headers=key)
    read = await client.get(f"{PATH}/{created.json()['id']}", headers=key)

    assert created.status_code == 201
    assert created.json()["created_by"]["type"] == "service_account"
    assert "margin" not in read.json()["lines"][0]
    assert read.json()["lines"][0]["below_margin_floor"] is True


async def test_an_integration_without_quote_scopes_is_refused(client: httpx.AsyncClient) -> None:
    key = await api_key(client, "catalog:read")

    response = await client.post(PATH, json=new_quote(), headers=key)

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


async def test_a_quote_is_read_with_its_etag(client: httpx.AsyncClient) -> None:
    created = await client.post(PATH, json=new_quote(), headers=bearer())

    response = await client.get(f"{PATH}/{created.json()['id']}", headers=bearer(Role.ADMIN))

    assert response.status_code == 200
    assert response.headers["etag"] == '"1"'
    assert response.json() == created.json()


async def test_an_unknown_quote_is_a_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{PATH}/{uuid.uuid7()}", headers=bearer())

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"customer_id": str(uuid.uuid7())}, "unknown_customer"),
        ({"lines": [{"product_id": str(uuid.uuid7()), "quantity": "1"}]}, "unknown_product"),
        ({"lines": [{"product_id": str(BOLTS.id), "quantity": "0"}]}, "invalid_quantity"),
        ({"valid_until": "2026-10-02"}, "invalid_quote"),
        ({"valid_until": "next week"}, "validation_error"),
        ({"price": "1"}, "validation_error"),
        ({"lines": [{"product_id": str(BOLTS.id), "quantity": "1"}] * 101}, "validation_error"),
    ],
    ids=["customer", "product", "quantity", "past", "date", "extra", "too-many-lines"],
)
async def test_invalid_quotes_are_a_422(
    client: httpx.AsyncClient, changes: dict[str, object], code: str
) -> None:
    response = await client.post(PATH, json=new_quote(**changes), headers=bearer())

    assert response.status_code == 422
    assert response.json()["code"] == code


async def test_numbers_keep_counting_and_skip_nothing(client: httpx.AsyncClient) -> None:
    await client.post(PATH, json=new_quote(), headers=bearer())
    await client.post(PATH, json=new_quote(valid_until="2026-01-01"), headers=bearer())  # refused

    response = await client.post(PATH, json=new_quote(), headers=bearer())

    assert response.json()["number"] == "NF-2026-000002"


async def test_quotes_are_listed_newest_first_without_their_lines(
    client: httpx.AsyncClient,
) -> None:
    first = await client.post(PATH, json=new_quote(), headers=bearer())
    second = await client.post(PATH, json=new_quote(), headers=bearer())

    response = await client.get(PATH, headers=bearer())

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == [second.json()["id"], first.json()["id"]]
    assert items[0] | {"id": None, "created_by": None} == {
        "id": None,
        "number": "NF-2026-000002",
        "revision": 1,
        "customer_id": str(ACME.id),
        "status": "draft",
        "valid_until": "2026-11-02",
        "net_subtotal": usd("900.0000"),
        "total": usd("965.2500"),
        "created_by": None,
        "created_at": "2026-10-03T12:00:00Z",
        "status_changed_at": "2026-10-03T12:00:00Z",
        "version": 1,
    }
    assert response.json()["next_cursor"] is None


async def test_quotes_are_filtered_sorted_and_paged(client: httpx.AsyncClient) -> None:
    late = await client.post(PATH, json=new_quote(valid_until="2026-12-31"), headers=bearer())
    soon = await client.post(PATH, json=new_quote(valid_until="2026-10-10"), headers=bearer())
    params = {"sort": "valid_until", "limit": "1", "status": "draft"}

    page = await client.get(PATH, params=params, headers=bearer())
    rest = await client.get(
        PATH, params=params | {"cursor": page.json()["next_cursor"]}, headers=bearer()
    )
    by_number = await client.get(PATH, params={"number": "NF-2026-000001"}, headers=bearer())

    assert [item["id"] for item in page.json()["items"]] == [soon.json()["id"]]
    assert [item["id"] for item in rest.json()["items"]] == [late.json()["id"]]
    assert [item["id"] for item in by_number.json()["items"]] == [late.json()["id"]]


async def test_a_cursor_from_another_list_is_a_422(client: httpx.AsyncClient) -> None:
    await client.post(PATH, json=new_quote(), headers=bearer())
    await client.post(PATH, json=new_quote(), headers=bearer())
    page = await client.get(PATH, params={"limit": "1"}, headers=bearer())

    response = await client.get(
        PATH,
        params={"limit": "1", "sort": "valid_until", "cursor": page.json()["next_cursor"]},
        headers=bearer(),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_cursor"


async def patch(
    client: httpx.AsyncClient,
    quote_id: str,
    body: dict[str, object],
    *,
    if_match: str | None = '"1"',
) -> httpx.Response:
    headers = bearer() | ({"If-Match": if_match} if if_match is not None else {})
    return await client.patch(f"{PATH}/{quote_id}", json=body, headers=headers)


async def test_a_draft_gets_new_terms_with_its_etag(client: httpx.AsyncClient) -> None:
    created = await client.post(PATH, json=new_quote(notes="Rush order"), headers=bearer())

    response = await patch(
        client, created.json()["id"], {"valid_until": "2026-12-01", "notes": None}
    )

    assert response.status_code == 200
    assert response.headers["etag"] == '"2"'
    assert (response.json()["valid_until"], response.json()["notes"]) == ("2026-12-01", None)
    assert response.json()["priced_at"] == created.json()["priced_at"]  # terms do not reprice


async def test_terms_changed_with_an_old_etag_are_a_412(client: httpx.AsyncClient) -> None:
    created = await client.post(PATH, json=new_quote(), headers=bearer())
    await patch(client, created.json()["id"], {"notes": "First"})

    response = await patch(client, created.json()["id"], {"notes": "Second"})

    assert response.status_code == 412
    assert response.json()["code"] == "stale_version"


async def test_terms_without_an_etag_are_a_428(client: httpx.AsyncClient) -> None:
    created = await client.post(PATH, json=new_quote(), headers=bearer())

    response = await patch(client, created.json()["id"], {"notes": "No ETag"}, if_match=None)

    assert response.status_code == 428


async def test_terms_of_a_submitted_quote_are_a_409(
    client: httpx.AsyncClient, database: InMemoryDatabase
) -> None:
    created = await client.post(PATH, json=new_quote(), headers=bearer())
    database.quotes[uuid.UUID(created.json()["id"])].status = QuoteStatus.APPROVED

    response = await patch(client, created.json()["id"], {"notes": "Too late"})

    assert response.status_code == 409
    assert response.json()["code"] == "quote_not_editable"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({}, "validation_error"),
        ({"valid_until": "2026-10-02"}, "invalid_quote"),
        ({"number": "NF-1"}, "validation_error"),
    ],
    ids=["empty", "past", "extra"],
)
async def test_invalid_terms_are_a_422(
    client: httpx.AsyncClient, body: dict[str, object], code: str
) -> None:
    created = await client.post(PATH, json=new_quote(), headers=bearer())

    response = await patch(client, created.json()["id"], body)

    assert response.status_code == 422
    assert response.json()["code"] == code
