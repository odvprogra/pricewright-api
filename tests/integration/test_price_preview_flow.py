"""A manager's rules priced by a preview, against the fully wired application and PostgreSQL."""

import httpx
import pytest

from tests.integration.data import ADMIN_EMAIL, ADMIN_PASSWORD

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("session_factory")]


async def test_rules_kept_in_postgresql_price_a_preview(
    northfield_client: httpx.AsyncClient,
) -> None:
    client = northfield_client
    login = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    admin = {"Authorization": f"Bearer {login.json()['access_token']}"}
    category = await client.post(
        "/api/v1/product-categories", json={"name": "Fasteners"}, headers=admin
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
        headers=admin,
    )
    customer = await client.post(
        "/api/v1/customers",
        json={"account_number": "C-1001", "name": "Acme Industrial", "tier": "gold"},
        headers=admin,
    )
    for rule in [
        {
            "kind": "volume_tier",
            "name": "Fasteners in bulk",
            "category_id": category.json()["id"],
            "valid_from": "2026-01-01T00:00:00Z",
            "brackets": [{"min_quantity": "100", "rate": "0.1"}],
        },
        {
            "kind": "customer_tier",
            "name": "Gold customers",
            "customer_tier": "gold",
            "rate": "0.05",
            "valid_from": "2026-01-01T00:00:00Z",
        },
        {
            "kind": "promotion",
            "name": "Expired",
            "rate": "0.5",
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_to": "2026-02-01T00:00:00Z",
        },
    ]:
        created = await client.post("/api/v1/pricing-rules", json=rule, headers=admin)
        assert created.status_code == 201, created.text

    response = await client.post(
        "/api/v1/pricing/preview",
        json={
            "customer_id": customer.json()["id"],
            "lines": [{"product_id": product.json()["id"], "quantity": "120"}],
        },
        headers=admin,
    )

    assert response.status_code == 200, response.text
    [line] = response.json()["lines"]
    # 12.50 → 11.25 (10% from 100 boxes) → 10.6875 (5% for gold): 120 x 10.6875 = 1282.50.
    assert [step["unit_price"]["amount"] for step in line["steps"]] == ["11.2500", "10.6875"]
    assert line["net_total"]["amount"] == "1282.5000"
    assert response.json()["tax"]["amount"] == "89.7800"  # 7% of 1282.50 = 89.775, half up
    # A 14.5% discount does not exceed the 15% threshold; the expired promotion did not apply.
    assert (response.json()["discount"], response.json()["requires_approval"]) == ("0.1450", False)
