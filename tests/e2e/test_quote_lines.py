"""Quote lines over HTTP, with in-memory adapters: the flow `erp-mcp-server` drives."""

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest

from pricewright.api.app import create_app
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.users import Role
from tests.e2e.test_quotes import (
    ACME,
    BOLTS,
    FLOOR,
    NORTHFIELD,
    PATH,
    VOLUME,
    api_key,
    bearer,
    new_quote,
    usd,
)
from tests.fakes import FakeClock, InMemoryDatabase, fake_services


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


async def draft(client: httpx.AsyncClient, headers: dict[str, str] | None = None) -> str:
    created = await client.post(PATH, json=new_quote(), headers=headers or bearer())
    return str(created.json()["id"])


def lines(quote_id: str) -> str:
    return f"{PATH}/{quote_id}/lines"


async def test_a_line_is_added_and_every_line_priced_again(client: httpx.AsyncClient) -> None:
    quote_id = await draft(client)

    response = await client.post(
        lines(quote_id),
        json={"product_id": str(BOLTS.id), "quantity": "2"},
        headers=bearer() | {"If-Match": '"1"'},
    )

    assert response.status_code == 200
    assert response.headers["etag"] == '"2"'
    body = response.json()
    assert [line["quantity"] for line in body["lines"]] == ["10.000", "2.000"]  # new line last
    assert body["net_subtotal"] == usd("1100.0000")


async def test_lines_need_the_current_etag(client: httpx.AsyncClient) -> None:
    quote_id = await draft(client)
    line = {"product_id": str(BOLTS.id), "quantity": "1"}

    stale = await client.post(lines(quote_id), json=line, headers=bearer() | {"If-Match": '"7"'})
    missing = await client.post(lines(quote_id), json=line, headers=bearer())

    assert (stale.status_code, missing.status_code) == (412, 428)


async def test_two_concurrent_edits_the_second_gets_a_412(client: httpx.AsyncClient) -> None:
    # Brief §7: both read version 1; the first edit wins, the second must reload.
    quote_id = await draft(client)
    line = {"product_id": str(BOLTS.id), "quantity": "1"}
    read_by_both = {"If-Match": '"1"'}

    first = await client.post(lines(quote_id), json=line, headers=bearer() | read_by_both)
    second = await client.post(lines(quote_id), json=line, headers=bearer() | read_by_both)

    assert (first.status_code, second.status_code) == (200, 412)
    assert second.json()["code"] == "stale_version"


async def test_a_manager_overrides_a_line_with_a_reason(client: httpx.AsyncClient) -> None:
    manager_id = uuid.uuid7()
    quote_id = await draft(client)

    response = await client.post(
        lines(quote_id),
        json={
            "product_id": str(BOLTS.id),
            "quantity": "1",
            "override": {"unit_price": usd("75"), "reason": "Matching a bid"},
        },
        headers=bearer(Role.SALES_MANAGER, manager_id) | {"If-Match": '"1"'},
    )

    assert response.status_code == 200
    line = response.json()["lines"][-1]
    assert line["override"] == {
        "rate": None,
        "unit_price": usd("75.0000"),
        "reason": "Matching a bid",
        "set_by": str(manager_id),
    }
    assert line["steps"][-1]["stage"] == "manual_override"
    assert line["net_unit_price"] == usd("75.0000")


async def test_reps_and_integrations_cannot_override(client: httpx.AsyncClient) -> None:
    key = await api_key(client, "quotes:read", "quotes:manage")
    quote_id = await draft(client)
    line = {
        "product_id": str(BOLTS.id),
        "quantity": "1",
        "override": {"rate": "0.3", "reason": "Generous"},
    }

    by_rep = await client.post(lines(quote_id), json=line, headers=bearer() | {"If-Match": '"1"'})
    by_key = await client.post(lines(quote_id), json=line, headers=key | {"If-Match": '"1"'})
    on_create = await client.post(PATH, json=new_quote(lines=[line]), headers=bearer())

    assert (by_rep.status_code, by_key.status_code, on_create.status_code) == (403, 403, 403)
    assert by_rep.json()["code"] == "permission_denied"


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"rate": "0.1", "unit_price": usd("75"), "reason": "Both"}, "validation_error"),
        ({"reason": "Neither"}, "validation_error"),
        ({"rate": "0.1", "reason": ""}, "validation_error"),
        ({"rate": "1.5", "reason": "Too much"}, "invalid_override"),
        (
            {"unit_price": {"amount": "75", "currency": "EUR"}, "reason": "Euros"},
            "invalid_override",
        ),
    ],
    ids=["both", "neither", "no-reason", "rate", "currency"],
)
async def test_invalid_overrides_are_a_422(
    client: httpx.AsyncClient, override: dict[str, object], code: str
) -> None:
    quote_id = await draft(client)

    response = await client.post(
        lines(quote_id),
        json={"product_id": str(BOLTS.id), "quantity": "1", "override": override},
        headers=bearer(Role.SALES_MANAGER) | {"If-Match": '"1"'},
    )

    assert response.status_code == 422
    assert response.json()["code"] == code


async def test_a_line_quantity_changes_and_its_override_is_removed(
    client: httpx.AsyncClient,
) -> None:
    manager = bearer(Role.SALES_MANAGER)
    created = await client.post(
        PATH,
        json=new_quote(
            lines=[
                {
                    "product_id": str(BOLTS.id),
                    "quantity": "10",
                    "override": {"rate": "0.05", "reason": "Loyalty"},
                }
            ]
        ),
        headers=manager,
    )
    quote_id, line_id = created.json()["id"], created.json()["lines"][0]["id"]

    quantity = await client.patch(
        f"{lines(quote_id)}/{line_id}",
        json={"quantity": "20"},
        headers=bearer() | {"If-Match": '"1"'},
    )
    cleared = await client.patch(
        f"{lines(quote_id)}/{line_id}",
        json={"override": None},
        headers=manager | {"If-Match": '"2"'},
    )

    assert quantity.status_code == 200
    assert quantity.json()["lines"][0]["quantity"] == "20.000"
    assert quantity.json()["lines"][0]["override"]["reason"] == "Loyalty"  # kept
    assert cleared.json()["lines"][0]["override"] is None
    assert cleared.headers["etag"] == '"3"'


async def test_a_rep_cannot_remove_a_managers_override(client: httpx.AsyncClient) -> None:
    quote_id = await draft(client)
    line_id = (await client.get(f"{PATH}/{quote_id}", headers=bearer())).json()["lines"][0]["id"]

    response = await client.patch(
        f"{lines(quote_id)}/{line_id}",
        json={"override": None},
        headers=bearer() | {"If-Match": '"1"'},
    )

    assert response.status_code == 403


async def test_an_empty_line_patch_is_a_422(client: httpx.AsyncClient) -> None:
    quote_id = await draft(client)
    line_id = (await client.get(f"{PATH}/{quote_id}", headers=bearer())).json()["lines"][0]["id"]

    response = await client.patch(
        f"{lines(quote_id)}/{line_id}", json={}, headers=bearer() | {"If-Match": '"1"'}
    )

    assert response.status_code == 422


async def test_an_integration_adds_and_removes_lines(client: httpx.AsyncClient) -> None:
    key = await api_key(client, "quotes:read", "quotes:manage")
    quote_id = await draft(client, key)

    added = await client.post(
        lines(quote_id),
        json={"product_id": str(BOLTS.id), "quantity": "3"},
        headers=key | {"If-Match": '"1"'},
    )
    new_line = added.json()["lines"][-1]["id"]
    removed = await client.delete(
        f"{lines(quote_id)}/{new_line}", headers=key | {"If-Match": '"2"'}
    )

    assert removed.status_code == 200
    assert removed.headers["etag"] == '"3"'
    assert [line["quantity"] for line in removed.json()["lines"]] == ["10.000"]
    assert all("margin" not in line for line in removed.json()["lines"])


async def test_an_unknown_line_is_a_404(client: httpx.AsyncClient) -> None:
    quote_id = await draft(client)

    response = await client.delete(
        f"{lines(quote_id)}/{uuid.uuid7()}", headers=bearer() | {"If-Match": '"1"'}
    )

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_lines_of_a_submitted_quote_are_a_409(
    client: httpx.AsyncClient, database: InMemoryDatabase
) -> None:
    quote_id = await draft(client)
    database.quotes[uuid.UUID(quote_id)].status = QuoteStatus.APPROVED

    response = await client.post(
        lines(quote_id),
        json={"product_id": str(BOLTS.id), "quantity": "1"},
        headers=bearer() | {"If-Match": '"1"'},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "quote_not_editable"
