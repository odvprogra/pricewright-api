"""Approvals over HTTP, with in-memory adapters: the inbox, four eyes and decisions (ADR-0020)."""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest

from pricewright.api.app import create_app
from pricewright.domain.pricing_rules import Bracket, PricingRule, RuleKind
from pricewright.domain.users import Role
from tests.e2e.test_quotes import ACME, BOLTS, NORTHFIELD, PATH, SINCE, api_key, bearer, new_quote
from tests.fakes import FakeClock, InMemoryDatabase, fake_services

INBOX = "/api/v1/approval-requests"
# 20% off from ten units: above the tenant's 15% threshold (brief §7).
GENEROUS_VOLUME = PricingRule.create(
    tenant_id=NORTHFIELD.id,
    kind=RuleKind.VOLUME_TIER,
    name="Big buyers",
    valid_from=SINCE,
    brackets=[Bracket(Decimal(10), Decimal("0.2"))],
)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    database = InMemoryDatabase(
        tenants={NORTHFIELD.id: NORTHFIELD},
        products={BOLTS.id: BOLTS},
        customers={ACME.id: ACME},
        pricing_rules={GENEROUS_VOLUME.id: GENEROUS_VOLUME},
    )
    app = create_app(title="test", services=fake_services(database, FakeClock()))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def act(
    client: httpx.AsyncClient,
    quote: httpx.Response,
    action: str,
    headers: dict[str, str],
    **body: object,
) -> httpx.Response:
    return await client.post(
        f"{PATH}/{quote.json()['id']}/{action}",
        json=body,
        headers=headers | {"If-Match": quote.headers["etag"]},
    )


async def pending(client: httpx.AsyncClient, headers: dict[str, str]) -> httpx.Response:
    draft = await client.post(PATH, json=new_quote(), headers=headers)
    return await act(client, draft, "submit", headers)


async def test_a_20_percent_discount_is_approved_from_the_managers_inbox(
    client: httpx.AsyncClient,
) -> None:
    rep, manager_id = bearer(Role.SALES_REP), uuid.uuid7()
    manager = bearer(Role.SALES_MANAGER, manager_id)
    submitted = await pending(client, rep)
    assert submitted.json()["discount"] == "0.2000"
    assert submitted.json()["status"] == "pending_approval"

    inbox = await client.get(INBOX, headers=manager)
    [item] = inbox.json()["items"]
    quote = await client.get(f"{PATH}/{item['quote']['id']}", headers=manager)
    approved = await act(client, quote, "approve", manager, comment="Strategic account")
    after = await client.get(INBOX, headers=manager)

    assert (item["status"], item["reasons"]) == ("pending", ["discount_above_threshold"])
    assert item["quote"]["number"] == "NF-2026-000001"
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    decision = approved.json()["approvals"][0]
    assert (decision["status"], decision["decided_by"], decision["comment"]) == (
        "approved",
        str(manager_id),
        "Strategic account",
    )
    assert after.json()["items"] == []


async def test_a_manager_cannot_approve_their_own_quote(client: httpx.AsyncClient) -> None:
    author = bearer(Role.SALES_MANAGER)
    submitted = await pending(client, author)

    own = await act(client, submitted, "approve", author)
    other = await act(client, submitted, "approve", bearer(Role.SALES_MANAGER))

    assert own.status_code == 403
    assert own.json()["code"] == "self_approval"
    assert other.json()["status"] == "approved"


async def test_reps_and_integrations_cannot_approve(client: httpx.AsyncClient) -> None:
    submitted = await pending(client, bearer())
    key = await api_key(client, "quotes:read", "quotes:manage")

    by_rep = await act(client, submitted, "approve", bearer())
    by_key = await act(client, submitted, "approve", key)

    assert (by_rep.status_code, by_key.status_code) == (403, 403)
    assert by_rep.json()["code"] == "permission_denied"


async def test_a_rejection_says_what_to_change_and_the_rep_revises(
    client: httpx.AsyncClient,
) -> None:
    rep, manager = bearer(), bearer(Role.SALES_MANAGER)
    submitted = await pending(client, rep)

    silent = await act(client, submitted, "reject", manager)
    rejected = await act(client, submitted, "reject", manager, comment="Ten units at 15% at most")
    revised = await act(client, rejected, "revise", rep)

    assert silent.status_code == 422
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["approvals"][0]["comment"] == "Ten units at 15% at most"
    assert rejected.json()["allowed_actions"] == ["cancel", "revise"]
    assert (revised.status_code, revised.json()["number"]) == (201, "NF-2026-000001-R2")


async def test_a_draft_has_nothing_to_approve(client: httpx.AsyncClient) -> None:
    draft = await client.post(PATH, json=new_quote(), headers=bearer())

    response = await act(client, draft, "approve", bearer(Role.SALES_MANAGER))

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"


async def test_the_inbox_lists_decided_requests_and_pages(client: httpx.AsyncClient) -> None:
    manager = bearer(Role.SALES_MANAGER)
    first = await pending(client, bearer())
    await pending(client, bearer())
    await act(client, first, "approve", manager)
    await pending(client, bearer())

    approved = await client.get(INBOX, params={"status": "approved"}, headers=manager)
    page = await client.get(INBOX, params={"limit": "1"}, headers=manager)
    rest = await client.get(
        INBOX, params={"limit": "1", "cursor": page.json()["next_cursor"]}, headers=manager
    )
    wrong = await client.get(INBOX, params={"status": "maybe"}, headers=manager)

    assert [item["quote"]["number"] for item in approved.json()["items"]] == ["NF-2026-000001"]
    assert [item["quote"]["number"] for item in page.json()["items"]] == ["NF-2026-000002"]
    assert [item["quote"]["number"] for item in rest.json()["items"]] == ["NF-2026-000003"]
    assert rest.json()["next_cursor"] is None
    assert wrong.status_code == 422


async def test_an_integration_reads_the_inbox_with_quotes_read(client: httpx.AsyncClient) -> None:
    await pending(client, bearer())
    key = await api_key(client, "quotes:read")
    no_scope = await api_key(client, "catalog:read")

    allowed = await client.get(INBOX, headers=key)
    refused = await client.get(INBOX, headers=no_scope)

    assert len(allowed.json()["items"]) == 1
    assert refused.status_code == 403
