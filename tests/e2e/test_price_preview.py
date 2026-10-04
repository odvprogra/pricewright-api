"""Price previews over HTTP, with in-memory adapters."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from pricewright.api.app import create_app
from pricewright.application.pricing import MAX_PREVIEW_LINES
from pricewright.domain.auth import Principal
from pricewright.domain.catalog import Product, UnitOfMeasure
from pricewright.domain.customers import Customer, CustomerTier
from pricewright.domain.money import Money
from pricewright.domain.pricing_rules import Bracket, PricingRule, RuleKind
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeAccessTokens, FakeClock, InMemoryDatabase, fake_services

NORTHFIELD = Tenant.register(name="Northfield", settings=TenantSettings("USD", Decimal("0.0725")))
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
GOLD = PricingRule.create(
    tenant_id=NORTHFIELD.id,
    kind=RuleKind.CUSTOMER_TIER,
    name="Gold customers",
    rate=Decimal("0.05"),
    valid_from=SINCE,
    customer_tier=CustomerTier.GOLD,
)
FLOOR = PricingRule.create(
    tenant_id=NORTHFIELD.id,
    kind=RuleKind.MARGIN_FLOOR,
    name="Tenant floor",
    rate=Decimal("0.15"),
    valid_from=SINCE,
)
PATH = "/api/v1/pricing/preview"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    database = InMemoryDatabase(
        tenants={NORTHFIELD.id: NORTHFIELD},
        products={BOLTS.id: BOLTS},
        customers={ACME.id: ACME},
        pricing_rules={rule.id: rule for rule in (VOLUME, GOLD, FLOOR)},
    )
    app = create_app(title="test", services=fake_services(database, FakeClock()))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def bearer(role: Role = Role.SALES_REP) -> dict[str, str]:
    token = FakeAccessTokens().issue(Principal(NORTHFIELD.id, uuid.uuid7(), role)).token
    return {"Authorization": f"Bearer {token}"}


def request(quantity: object = "10", **changes: object) -> dict[str, object]:
    return {
        "customer_id": str(ACME.id),
        "lines": [{"product_id": str(BOLTS.id), "quantity": quantity}],
    } | changes


def usd(amount: str) -> dict[str, str]:
    return {"amount": amount, "currency": "USD"}


async def test_a_rep_sees_each_step_the_totals_and_why_approval_is_needed(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(PATH, json=request(), headers=bearer())

    assert response.status_code == 200, response.text
    body = response.json()
    [line] = body["lines"]
    assert [
        (s["stage"], s["label"], s["rate"], s["amount"], s["unit_price"]) for s in line["steps"]
    ] == [
        ("volume_tier", "Bulk", "0.1000", usd("-10.0000"), usd("90.0000")),
        ("customer_tier", "Gold customers", "0.0500", usd("-4.5000"), usd("85.5000")),
    ]
    assert (line["sku"], line["quantity"], line["net_total"]) == (
        "FAS-M6-100",
        "10.000",
        usd("855.0000"),
    )
    # 855 sold for 800 of cost: a 6.43% margin, below the 15% floor.
    assert line["margin"] == {"amount": usd("55.0000"), "rate": "0.0643"}
    assert (line["margin_floor"]["label"], line["below_margin_floor"]) == ("Tenant floor", True)
    assert (body["list_subtotal"], body["tax"], body["total"]) == (
        usd("1000.0000"),
        usd("61.9900"),
        usd("916.9900"),
    )
    assert (body["discount"], body["requires_approval"]) == ("0.1450", True)
    assert body["approval_reasons"] == ["line_below_margin_floor"]
    assert body["priced_at"] == "2026-10-03T12:00:00Z"


async def test_an_integration_previews_prices_without_margins(client: httpx.AsyncClient) -> None:
    admin = bearer(Role.ADMIN)
    account = await client.post(
        "/api/v1/service-accounts",
        json={"name": "erp-mcp-server", "scopes": ["pricing:read"]},
        headers=admin,
    )
    key = await client.post(
        f"/api/v1/service-accounts/{account.json()['id']}/keys", json={}, headers=admin
    )

    response = await client.post(
        PATH, json=request(), headers={"Authorization": f"Bearer {key.json()['key']}"}
    )

    [line] = response.json()["lines"]
    assert "margin" not in line
    assert line["below_margin_floor"] is True  # it still learns that approval will be needed
    assert response.json()["requires_approval"] is True


async def test_rules_are_those_effective_at_the_pricing_time(client: httpx.AsyncClient) -> None:
    before = request(priced_at="2026-09-30T23:00:00-04:00")  # 03:00 UTC on October 1

    response = await client.post(PATH, json=before, headers=bearer())

    assert response.json()["priced_at"] == "2026-10-01T03:00:00Z"
    assert [step["stage"] for step in response.json()["lines"][0]["steps"]] == [
        "volume_tier",
        "customer_tier",
    ]


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (request(customer_id=str(uuid.uuid7())), "unknown_customer"),
        (
            request(lines=[{"product_id": str(uuid.uuid7()), "quantity": "1"}]),
            "unknown_product",
        ),
        (request(quantity="0"), "invalid_quantity"),
        (request(quantity="1.0001"), "invalid_quantity"),
        (request(lines=[]), "validation_error"),
        (
            request(
                lines=[{"product_id": str(BOLTS.id), "quantity": "1"}] * (MAX_PREVIEW_LINES + 1)
            ),
            "validation_error",
        ),
        (request(priced_at="2026-10-01T00:00:00"), "validation_error"),
    ],
    ids=[
        "unknown-customer",
        "unknown-product",
        "zero",
        "four-places",
        "no-lines",
        "too-many-lines",
        "naive-time",
    ],
)
async def test_invalid_previews_are_a_422(
    client: httpx.AsyncClient, body: dict[str, object], code: str
) -> None:
    response = await client.post(PATH, json=body, headers=bearer())

    assert response.status_code == 422
    assert response.json()["code"] == code


async def test_a_preview_needs_credentials(client: httpx.AsyncClient) -> None:
    response = await client.post(PATH, json=request())

    assert response.status_code == 401
