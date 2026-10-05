"""A quote's lifecycle over HTTP, with in-memory adapters (ADR-0005, ADR-0020)."""

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest

from pricewright.api.app import create_app
from pricewright.domain.users import Role
from tests.e2e.test_quotes import (
    ACME,
    BOLTS,
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
def clock() -> FakeClock:
    return FakeClock()  # 2026-10-03 12:00 UTC


@pytest.fixture
async def client(clock: FakeClock) -> AsyncIterator[httpx.AsyncClient]:
    database = InMemoryDatabase(
        tenants={NORTHFIELD.id: NORTHFIELD},
        products={BOLTS.id: BOLTS},
        customers={ACME.id: ACME},
        pricing_rules={VOLUME.id: VOLUME},  # no floor: 10 bolts need no approval
    )
    app = create_app(title="test", services=fake_services(database, clock))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def act(
    client: httpx.AsyncClient,
    quote: httpx.Response,
    action: str,
    headers: dict[str, str] | None = None,
    **body: object,
) -> httpx.Response:
    return await client.post(
        f"{PATH}/{quote.json()['id']}/{action}",
        json=body or None,
        headers=(headers or bearer()) | {"If-Match": quote.headers["etag"]},
    )


async def test_a_quote_goes_from_draft_to_accepted(client: httpx.AsyncClient) -> None:
    draft = await client.post(PATH, json=new_quote(), headers=bearer())

    submitted = await act(client, draft, "submit")
    sent = await act(client, submitted, "send")
    accepted = await act(client, sent, "accept")

    assert [r.status_code for r in (submitted, sent, accepted)] == [200, 200, 200]
    assert [r.json()["status"] for r in (submitted, sent, accepted)] == [
        "approved",
        "sent",
        "accepted",
    ]
    assert accepted.headers["etag"] == '"4"'
    assert submitted.json()["allowed_actions"] == ["cancel", "revise", "send"]
    assert accepted.json()["allowed_actions"] == ["convert"]


async def test_a_generous_quote_waits_for_approval_with_its_request(
    client: httpx.AsyncClient,
) -> None:
    manager = bearer(Role.SALES_MANAGER)
    line = {
        "product_id": str(BOLTS.id),
        "quantity": "10",
        "override": {"rate": "0.3", "reason": "Clearance"},
    }
    draft = await client.post(PATH, json=new_quote(lines=[line]), headers=manager)

    submitted = await act(client, draft, "submit")

    assert submitted.json()["status"] == "pending_approval"
    [request] = submitted.json()["approvals"]
    assert request | {"id": None, "requested_by": None} == {
        "id": None,
        "status": "pending",
        "requested_by": None,
        "requested_at": "2026-10-03T12:00:00Z",
        "reasons": ["discount_above_threshold"],
        "discount": "0.3700",
        "approval_threshold": "0.1500",
        "list_subtotal": usd("1000.0000"),
        "net_subtotal": usd("630.0000"),
        "decided_by": None,
        "decided_at": None,
        "comment": None,
    }
    assert submitted.json()["allowed_actions"] == ["approve", "cancel", "recall", "reject"]


async def test_a_pending_quote_is_recalled_to_draft(client: httpx.AsyncClient) -> None:
    manager = bearer(Role.SALES_MANAGER)
    line = {
        "product_id": str(BOLTS.id),
        "quantity": "10",
        "override": {"rate": "0.3", "reason": "Clearance"},
    }
    draft = await client.post(PATH, json=new_quote(lines=[line]), headers=manager)
    submitted = await act(client, draft, "submit")

    recalled = await act(client, submitted, "recall")

    assert recalled.json()["status"] == "draft"
    assert recalled.json()["approvals"][0]["status"] == "withdrawn"


async def test_a_customers_no_is_a_cancellation_with_a_reason(client: httpx.AsyncClient) -> None:
    draft = await client.post(PATH, json=new_quote(), headers=bearer())
    sent = await act(client, await act(client, draft, "submit"), "send")

    without = await act(client, sent, "cancel")
    cancelled = await act(client, sent, "cancel", reason="Customer chose another bid")

    assert without.status_code == 422
    assert (cancelled.json()["status"], cancelled.json()["cancel_reason"]) == (
        "cancelled",
        "Customer chose another bid",
    )
    assert cancelled.json()["allowed_actions"] == []


async def test_revising_supersedes_the_quote_with_its_next_revision(
    client: httpx.AsyncClient,
) -> None:
    draft = await client.post(PATH, json=new_quote(), headers=bearer())
    sent = await act(client, await act(client, draft, "submit"), "send")

    revised = await act(client, sent, "revise")
    old = await client.get(f"{PATH}/{sent.json()['id']}", headers=bearer())

    assert revised.status_code == 201
    assert revised.headers["location"] == f"{PATH}/{revised.json()['id']}"
    assert revised.headers["etag"] == '"1"'
    assert (revised.json()["number"], revised.json()["status"]) == ("NF-2026-000001-R2", "draft")
    assert revised.json()["supersedes_id"] == sent.json()["id"]
    assert (old.json()["status"], old.json()["superseded_by_id"]) == (
        "superseded",
        revised.json()["id"],
    )


async def test_an_integration_submits_but_never_sends(client: httpx.AsyncClient) -> None:
    key = await api_key(client, "quotes:read", "quotes:manage")
    draft = await client.post(PATH, json=new_quote(), headers=key)

    submitted = await act(client, draft, "submit", key)
    sent = await act(client, submitted, "send", key)

    assert submitted.status_code == 200
    assert sent.status_code == 403
    assert sent.json()["code"] == "permission_denied"


@pytest.mark.parametrize(
    ("action", "code"),
    [("accept", "invalid_transition"), ("send", "invalid_transition")],
)
async def test_a_draft_cannot_skip_ahead(client: httpx.AsyncClient, action: str, code: str) -> None:
    draft = await client.post(PATH, json=new_quote(), headers=bearer())

    response = await act(client, draft, action)

    assert response.status_code == 409
    assert response.json()["code"] == code


async def test_an_empty_draft_cannot_be_submitted(client: httpx.AsyncClient) -> None:
    draft = await client.post(PATH, json=new_quote(lines=[]), headers=bearer())

    response = await act(client, draft, "submit")

    assert response.status_code == 409
    assert response.json()["code"] == "quote_empty"


async def test_an_expired_offer_can_only_be_revised(
    client: httpx.AsyncClient, clock: FakeClock
) -> None:
    draft = await client.post(PATH, json=new_quote(), headers=bearer())
    sent = await act(client, await act(client, draft, "submit"), "send")
    clock.advance(timedelta(days=31))  # valid until November 2

    accepted = await act(client, sent, "accept")
    read = await client.get(f"{PATH}/{sent.json()['id']}", headers=bearer())
    revised = await act(client, sent, "revise")

    assert accepted.status_code == 409
    assert accepted.json()["code"] == "quote_expired"
    assert read.json()["allowed_actions"] == ["revise"]
    assert revised.status_code == 201
    assert revised.json()["valid_until"] == "2026-12-03"  # a fresh validity


async def test_transitions_need_the_current_etag(client: httpx.AsyncClient) -> None:
    draft = await client.post(PATH, json=new_quote(), headers=bearer())
    path = f"{PATH}/{draft.json()['id']}/submit"

    stale = await client.post(path, headers=bearer() | {"If-Match": '"9"'})
    missing = await client.post(path, headers=bearer())

    assert (stale.status_code, missing.status_code) == (412, 428)


async def test_an_unknown_quote_cannot_move(client: httpx.AsyncClient) -> None:
    response = await client.post(
        f"{PATH}/{uuid.uuid7()}/submit", headers=bearer() | {"If-Match": '"1"'}
    )

    assert response.status_code == 404
