"""Converting quotes into orders against PostgreSQL (ADR-0022, ADR-0023): the brief's acceptance
criterion over HTTP, and two conversions with the same Idempotency-Key at once."""

import asyncio
import hashlib
import uuid
from datetime import datetime

import httpx
import pytest
from sqlalchemy import text

from pricewright.application.idempotency import Created
from pricewright.application.ports import IdempotencyKeyRepository, UnitOfWork, UnitOfWorkFactory
from pricewright.application.quotes import convert_quote
from pricewright.domain.actors import Actor
from pricewright.domain.auth import Principal
from pricewright.domain.errors import DomainError
from pricewright.domain.idempotency import (
    IdempotencyKeyInUseError,
    IdempotencyRecord,
    IdempotentRequest,
)
from pricewright.domain.orders import Order
from pricewright.domain.users import Role
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import ADMIN_EMAIL, ADMIN_PASSWORD, Sessions
from tests.integration.stock import NOW, accepted, stock

pytestmark = pytest.mark.integration


async def test_retrying_a_conversion_returns_the_same_order_never_two(
    northfield_client: httpx.AsyncClient, session_factory: Sessions
) -> None:
    """Brief §7, end to end: retrying "convert to order" with the same Idempotency-Key returns
    the same order, never two."""
    client = northfield_client
    login = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    admin = {"Authorization": f"Bearer {login.json()['access_token']}"}
    product = await client.post(
        "/api/v1/products",
        json={
            "sku": "FAS-M6-100",
            "name": "Hex bolt M6 x 100",
            "unit": "XBX",
            "list_price": {"amount": "12.50", "currency": "USD"},
            "unit_cost": {"amount": "7.25", "currency": "USD"},
        },
        headers=admin,
    )
    customer = await client.post(
        "/api/v1/customers", json={"account_number": "C-1001", "name": "Acme"}, headers=admin
    )
    quote = await client.post(
        "/api/v1/quotes",
        json={
            "customer_id": customer.json()["id"],
            "lines": [{"product_id": product.json()["id"], "quantity": "40"}],
        },
        headers=admin,
    )
    path = f"/api/v1/quotes/{quote.json()['id']}"
    for version, action in enumerate(("submit", "send", "accept"), start=1):
        moved = await client.post(f"{path}/{action}", headers=admin | {"If-Match": f'"{version}"'})
        assert moved.status_code == 200, moved.text
    keyed = admin | {"If-Match": '"4"', "Idempotency-Key": f'"{uuid.uuid4()}"'}

    first = await client.post(f"{path}/convert", json={}, headers=keyed)
    retry = await client.post(f"{path}/convert", json={}, headers=keyed)
    order = await client.get(first.headers["location"], headers=admin)

    assert (first.status_code, retry.status_code) == (201, 201), retry.text
    assert retry.headers["idempotent-replayed"] == "true"
    assert retry.json() == first.json() == order.json()
    assert first.json()["number"] == "ORD-2026-000001"
    assert first.json()["total"] == quote.json()["total"]
    async with session_factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM orders")) == 1


class _HoldAfterClaim:
    """Holds a claimed key until the test lets go, as a request still running would."""

    def __init__(self, inner: IdempotencyKeyRepository, held: asyncio.Barrier) -> None:
        self._inner = inner
        self._held = held

    async def claim(self, actor: Actor, key: str, *, now: datetime) -> IdempotencyRecord | None:
        record = await self._inner.claim(actor, key, now=now)
        await self._held.wait()
        return record

    async def add(self, record: IdempotencyRecord) -> None:
        await self._inner.add(record)


class _HoldingUnitOfWork(SqlAlchemyUnitOfWork):
    def __init__(self, sessions: Sessions, held: asyncio.Barrier) -> None:
        super().__init__(sessions)
        self._held = held

    async def __aenter__(self) -> _HoldingUnitOfWork:
        await super().__aenter__()
        self.idempotency_keys = _HoldAfterClaim(self.idempotency_keys, self._held)
        return self


async def test_two_conversions_with_one_key_at_once_make_one_order(
    session_factory: Sessions,
) -> None:
    northfield = await stock(session_factory)
    quote = await accepted(session_factory, northfield)
    rep = Principal(northfield.tenant.id, northfield.rep.id, Role.SALES_REP)
    request = IdempotentRequest(str(uuid.uuid4()), hashlib.sha256(b"{}").hexdigest())
    held = asyncio.Barrier(2)

    def holding() -> UnitOfWork:
        return _HoldingUnitOfWork(session_factory, held)

    def plain() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    async def attempt(unit_of_work: UnitOfWorkFactory) -> Created[Order] | DomainError:
        try:
            return await convert_quote(
                rep,
                quote.id,
                None,
                expected_version=1,  # a retry still carries the version read before converting
                unit_of_work=unit_of_work,
                clock=lambda: NOW,
                idempotency=request,
            )
        except DomainError as error:
            return error

    tasks = {asyncio.create_task(attempt(holding)), asyncio.create_task(attempt(holding))}
    # Whichever claims the key first waits at the barrier holding it, so the other one finds it
    # held and answers first, whatever the scheduling.
    done, running = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    [refused] = done
    await held.wait()  # now the holder goes on and commits
    [holder] = running
    converted = await holder
    retry = await attempt(plain)

    assert isinstance(refused.result(), IdempotencyKeyInUseError)
    assert isinstance(converted, Created)
    assert not converted.replayed
    assert isinstance(retry, Created)
    assert retry.replayed
    assert retry.value == converted.value
    async with session_factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM orders")) == 1
