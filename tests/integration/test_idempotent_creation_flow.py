"""Idempotency-Key against the fully wired application and PostgreSQL (ADR-0022): a retried draft
is created once, and a retry that arrives while the first request still holds the key is a 409."""

import uuid
from datetime import UTC, datetime

import httpx
import pytest

from pricewright.domain.actors import Actor
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import ADMIN_EMAIL, ADMIN_PASSWORD, Sessions

pytestmark = pytest.mark.integration

KEY = str(uuid.uuid4())  # generated: gitleaks flags literal keys


async def test_a_retried_draft_is_created_once_and_a_running_one_is_a_409(
    northfield_client: httpx.AsyncClient, session_factory: Sessions
) -> None:
    client = northfield_client
    login = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    admin = {"Authorization": f"Bearer {login.json()['access_token']}"}
    me = (await client.get("/api/v1/me", headers=admin)).json()
    customer = await client.post(
        "/api/v1/customers", json={"account_number": "C-1001", "name": "Acme"}, headers=admin
    )
    draft = {"customer_id": customer.json()["id"]}
    keyed = admin | {"Idempotency-Key": f'"{KEY}"'}

    first = await client.post("/api/v1/quotes", json=draft, headers=keyed)
    retry = await client.post("/api/v1/quotes", json=draft, headers=keyed)
    async with SqlAlchemyUnitOfWork(session_factory) as running:
        # Another request holds a second key: a retry of it is refused at once, not blocked.
        running.bind_tenant(uuid.UUID(me["tenant_id"]))
        held = str(uuid.uuid4())
        actor = Actor.person(uuid.UUID(me["id"]))
        await running.idempotency_keys.claim(actor, held, now=datetime.now(UTC))
        in_use = await client.post(
            "/api/v1/quotes", json=draft, headers=admin | {"Idempotency-Key": held}
        )
    listed = await client.get("/api/v1/quotes", headers=admin)

    assert (first.status_code, retry.status_code) == (201, 201), retry.text
    assert retry.headers["idempotent-replayed"] == "true"
    assert retry.json() == first.json()
    assert (in_use.status_code, in_use.json()["code"]) == (409, "idempotency_key_in_use")
    assert [quote["number"] for quote in listed.json()["items"]] == ["QUO-2026-000001"]
