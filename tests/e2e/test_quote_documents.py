"""A quote's PDF over HTTP (ADR-0027), with in-memory adapters and a fake renderer."""

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest

from pricewright.api.app import create_app
from pricewright.domain.users import Role
from tests.e2e.test_quotes import ACME, BOLTS, NORTHFIELD, PATH, api_key, bearer, new_quote
from tests.fakes import FakeClock, InMemoryDatabase, fake_services


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    database = InMemoryDatabase(
        tenants={NORTHFIELD.id: NORTHFIELD}, products={BOLTS.id: BOLTS}, customers={ACME.id: ACME}
    )
    app = create_app(title="test", services=fake_services(database, FakeClock()))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_a_quote_pdf_is_an_attachment_named_after_its_number(
    client: httpx.AsyncClient,
) -> None:
    created = await client.post(PATH, json=new_quote(), headers=bearer())

    response = await client.get(f"{PATH}/{created.json()['id']}/pdf", headers=bearer(Role.ADMIN))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="NF-2026-000001.pdf"'
    assert response.content == b"%PDF-fake NF-2026-000001 draft"


async def test_a_quote_pdf_needs_quotes_read(client: httpx.AsyncClient) -> None:
    created = await client.post(PATH, json=new_quote(), headers=bearer())
    key = await api_key(client, "catalog:read")

    response = await client.get(f"{PATH}/{created.json()['id']}/pdf", headers=key)

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


async def test_the_pdf_of_an_unknown_quote_is_a_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{PATH}/{uuid.uuid7()}/pdf", headers=bearer())

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
