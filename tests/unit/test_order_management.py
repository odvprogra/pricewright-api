"""Converting quotes into orders and reading them, through the use cases with in-memory fakes."""

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pricewright.application.idempotency import Created
from pricewright.application.orders import cancel_order, get_order
from pricewright.application.ports import UnitOfWork
from pricewright.application.quotes import convert_quote
from pricewright.domain.actors import Actor
from pricewright.domain.audit import AuditAction
from pricewright.domain.auth import Permission, PermissionDeniedError, Principal
from pricewright.domain.catalog import Product, UnitOfMeasure
from pricewright.domain.customers import Customer
from pricewright.domain.errors import DomainError, NotFoundError, StaleVersionError
from pricewright.domain.idempotency import (
    IdempotencyKeyInUseError,
    IdempotencyKeyReusedError,
    IdempotentRequest,
)
from pricewright.domain.money import Money
from pricewright.domain.numbering import NumberSeries
from pricewright.domain.orders import InvalidOrderError, Order, OrderStatus
from pricewright.domain.pricing import ArchivedCustomerError
from pricewright.domain.quote_lifecycle import InvalidTransitionError, QuoteStatus
from pricewright.domain.quotes import LineChange, PricingContext, Quote
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeClock, FakeUnitOfWork, InMemoryDatabase

KEY = str(uuid.uuid4())  # generated: gitleaks flags literal keys


def same_request(body: str = "{}", key: str = KEY) -> IdempotentRequest:
    """A fingerprint stands for the method, path and body the API hashes."""
    return IdempotentRequest(key, hashlib.sha256(body.encode()).hexdigest())


class Fixture:
    def __init__(self) -> None:
        self.clock = FakeClock()  # 2026-10-03 12:00 UTC
        self.northfield = Tenant.register(
            name="Northfield",
            settings=TenantSettings(
                "USD", Decimal("0.0725"), quote_prefix="NF", order_prefix="NFO"
            ),
        )
        self.bolts = Product.create(
            tenant_id=self.northfield.id,
            currency="USD",
            sku="FAS-M6-100",
            name="Hex bolt",
            unit=UnitOfMeasure.BOX,
            list_price=Money(Decimal(100), "USD"),
            unit_cost=Money(Decimal(60), "USD"),
        )
        self.acme = Customer.create(
            tenant_id=self.northfield.id,
            account_number="C-1001",
            name="Acme",
            tax_id="DE123456789",
            payment_terms_days=45,
        )
        self.rep = Principal(self.northfield.id, uuid.uuid7(), Role.SALES_REP)
        self.quote = self.accepted()
        self.database = InMemoryDatabase(
            tenants={self.northfield.id: self.northfield},
            products={self.bolts.id: self.bolts},
            customers={self.acme.id: self.acme},
            quotes={self.quote.id: self.quote},
        )

    def accepted(self) -> Quote:
        pricing = PricingContext(
            customer=self.acme,
            settings=self.northfield.settings,
            rules=(),
            products={self.bolts.id: self.bolts},
            at=self.clock.now,
        )
        quote = Quote.draft(
            number="NF-2026-000001",
            valid_until=self.clock.now.date(),
            by=Actor.of(self.rep),
            context=pricing,
            lines=[LineChange(self.bolts.id, Decimal(3))],
        )
        quote.submit(by=Actor.of(self.rep), context=pricing)
        quote.send(now=self.clock.now)
        quote.accept(now=self.clock.now)
        return quote

    def unit_of_work(self) -> UnitOfWork:
        return FakeUnitOfWork(self.database)

    async def convert(
        self,
        *,
        caller: Principal | None = None,
        version: int = 1,
        reference: str | None = None,
        idempotency: IdempotentRequest | None = None,
    ) -> Created[Order]:
        return await convert_quote(
            caller or self.rep,
            self.quote.id,
            reference,
            expected_version=version,
            unit_of_work=self.unit_of_work,
            clock=self.clock,
            idempotency=idempotency,
        )


@pytest.fixture
def f() -> Fixture:
    return Fixture()


async def test_convert_quote_makes_the_order_and_converts_the_quote(f: Fixture) -> None:
    created = await f.convert(reference="PO-4500123")

    order = created.value
    assert not created.replayed
    assert (order.number, order.quote_number, order.customer_reference) == (
        "NFO-2026-000001",
        "NF-2026-000001",
        "PO-4500123",
    )
    assert (order.customer.payment_terms_days, order.customer.tax_id) == (45, "DE123456789")
    assert f.database.orders == {order.id: order}
    stored = f.database.quotes[f.quote.id]
    assert (stored.status, stored.order_id, stored.version) == (
        QuoteStatus.CONVERTED,
        order.id,
        2,
    )
    assert f.database.document_numbers == {(f.northfield.id, NumberSeries.ORDER, 2026): 1}


async def test_convert_quote_records_both_events(f: Fixture) -> None:
    order = (await f.convert()).value

    events = {event.action: event for event in f.database.audit_events.values()}
    assert events.keys() == {AuditAction.QUOTE_CONVERTED, AuditAction.ORDER_CREATED}
    converted, created = events[AuditAction.QUOTE_CONVERTED], events[AuditAction.ORDER_CREATED]
    assert (converted.resource_id, created.resource_id) == (f.quote.id, order.id)
    assert converted.changes == {
        "status": ("accepted", "converted"),
        "order_id": (None, str(order.id)),
    }
    assert created.changes["number"] == (None, "NFO-2026-000001")
    assert created.changes["total"] == (None, "321.7500")  # 3 x 100 + 7.25% tax


@pytest.mark.parametrize(
    "scopes",
    [
        frozenset({Permission.ORDERS_MANAGE, Permission.ORDERS_READ}),  # people only (ADR-0023)
        frozenset({Permission.QUOTES_MANAGE, Permission.QUOTES_READ}),
    ],
)
async def test_convert_quote_is_for_people_with_orders_manage(
    f: Fixture, scopes: frozenset[Permission]
) -> None:
    integration = Principal(f.northfield.id, uuid.uuid7(), scopes=scopes)

    with pytest.raises(PermissionDeniedError, match="orders:manage"):
        await f.convert(caller=integration)

    assert f.database.orders == {}


async def test_convert_quote_refuses_a_quote_that_is_not_accepted(f: Fixture) -> None:
    f.database.quotes[f.quote.id].status = QuoteStatus.SENT

    with pytest.raises(InvalidTransitionError, match="cannot convert"):
        await f.convert()

    assert (f.database.orders, f.database.document_numbers) == ({}, {})  # no number taken


async def test_convert_quote_needs_the_current_version(f: Fixture) -> None:
    with pytest.raises(StaleVersionError):
        await f.convert(version=2)


async def test_convert_quote_of_another_tenant_is_not_found(f: Fixture) -> None:
    larkspur = Principal(uuid.uuid7(), uuid.uuid7(), Role.ADMIN)

    with pytest.raises(NotFoundError):
        await f.convert(caller=larkspur)


async def test_convert_quote_refuses_an_archived_customer(f: Fixture) -> None:
    f.database.customers[f.acme.id].change(is_active=False)

    with pytest.raises(ArchivedCustomerError):
        await f.convert()

    assert f.database.quotes[f.quote.id].status is QuoteStatus.ACCEPTED


async def test_convert_quote_retried_with_its_key_returns_the_same_order(f: Fixture) -> None:
    first = await f.convert(idempotency=same_request())
    # The retry carries the version the client read: stale now, since its own request moved it.
    retry = await f.convert(idempotency=same_request(), version=1)

    assert (first.replayed, retry.replayed) == (False, True)
    assert retry.value == first.value
    assert len(f.database.orders) == 1
    assert len(f.database.audit_events) == 2


async def test_convert_quote_refuses_its_key_for_another_request(f: Fixture) -> None:
    await f.convert(idempotency=same_request())

    with pytest.raises(IdempotencyKeyReusedError):
        await f.convert(idempotency=same_request('{"customer_reference":"PO-1"}'))


async def test_convert_quote_with_a_key_in_use_is_refused_at_once(f: Fixture) -> None:
    async with f.unit_of_work() as running:
        running.bind_tenant(f.northfield.id)
        await running.idempotency_keys.claim(Actor.of(f.rep), KEY, now=f.clock.now)

        with pytest.raises(IdempotencyKeyInUseError):
            await f.convert(idempotency=same_request())

    assert f.database.orders == {}


async def test_get_order_reads_an_order_with_orders_read(f: Fixture) -> None:
    order = (await f.convert()).value
    integration = Principal(
        f.northfield.id, uuid.uuid7(), scopes=frozenset({Permission.ORDERS_READ})
    )

    assert await get_order(integration, order.id, unit_of_work=f.unit_of_work) == order
    with pytest.raises(PermissionDeniedError, match="orders:read"):
        await get_order(
            Principal(f.northfield.id, uuid.uuid7(), scopes=frozenset()),
            order.id,
            unit_of_work=f.unit_of_work,
        )
    with pytest.raises(NotFoundError):
        await get_order(f.rep, uuid.uuid7(), unit_of_work=f.unit_of_work)


@dataclass(frozen=True)
class Attempt:
    key: str | None
    reference: str | None
    version: int


attempts = st.builds(
    Attempt,
    key=st.sampled_from([None, "key-1", "key-2"]),
    reference=st.sampled_from([None, "PO-1"]),
    version=st.sampled_from([1, 2]),
)


@settings(max_examples=150)
@given(st.lists(attempts, min_size=1, max_size=8))
def test_retries_never_make_a_second_order_and_get_the_same_one_back(
    sequence: list[Attempt],
) -> None:
    """Whatever keys, bodies and versions arrive, in any order: at most one order exists; every
    success names it; a key repeated with its first body gets it back; with another body, a 422."""
    f = Fixture()
    first_body: dict[str, str] = {}
    orders: set[uuid.UUID] = set()

    async def run() -> None:
        for attempt in sequence:
            body = f'{{"customer_reference":{attempt.reference!r}}}'
            request = None if attempt.key is None else same_request(body, attempt.key)
            known = attempt.key in first_body
            try:
                created = await f.convert(
                    reference=attempt.reference, version=attempt.version, idempotency=request
                )
            except IdempotencyKeyReusedError:
                assert known
                assert first_body[attempt.key or ""] != body
                continue
            except DomainError:
                assert not (known and first_body[attempt.key or ""] == body)
                continue
            orders.add(created.value.id)
            assert created.replayed == known
            if attempt.key is not None and not known:
                first_body[attempt.key] = body

    asyncio.run(run())

    assert len(f.database.orders) <= 1
    assert orders <= f.database.orders.keys()
    assert all(
        order.status is OrderStatus.OPEN and order.quote_id == f.quote.id
        for order in f.database.orders.values()
    )


async def cancel(
    f: Fixture, order: Order, *, version: int = 1, caller: Principal | None = None
) -> Order:
    return await cancel_order(
        caller or f.rep,
        order.id,
        "Entered for the wrong customer",
        expected_version=version,
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )


async def test_cancel_order_withdraws_it_and_records_the_reason(f: Fixture) -> None:
    order = (await f.convert()).value

    cancelled = await cancel(f, order)

    assert (cancelled.status, cancelled.cancel_reason, cancelled.version) == (
        OrderStatus.CANCELLED,
        "Entered for the wrong customer",
        2,
    )
    assert f.database.orders[order.id] == cancelled
    assert f.database.quotes[f.quote.id].status is QuoteStatus.CONVERTED  # the quote stays
    [event] = [
        e for e in f.database.audit_events.values() if e.action is AuditAction.ORDER_CANCELLED
    ]
    assert (event.resource_id, event.changes) == (
        order.id,
        {
            "status": ("open", "cancelled"),
            "cancel_reason": (None, "Entered for the wrong customer"),
        },
    )


async def test_cancel_order_is_refused_when_stale_cancelled_or_not_a_person(f: Fixture) -> None:
    order = (await f.convert()).value
    integration = Principal(
        f.northfield.id, uuid.uuid7(), scopes=frozenset({Permission.ORDERS_MANAGE})
    )

    with pytest.raises(StaleVersionError):
        await cancel(f, order, version=2)
    with pytest.raises(PermissionDeniedError, match="orders:manage"):
        await cancel(f, order, caller=integration)
    await cancel(f, order)
    with pytest.raises(InvalidTransitionError, match="cancelled"):
        await cancel(f, order, version=2)
    with pytest.raises(NotFoundError):
        await cancel_order(
            f.rep,
            uuid.uuid7(),
            "No such order",
            expected_version=1,
            unit_of_work=f.unit_of_work,
            clock=f.clock,
        )


async def test_cancel_order_needs_a_reason(f: Fixture) -> None:
    order = (await f.convert()).value

    with pytest.raises(InvalidOrderError, match="reason"):
        await cancel_order(
            f.rep, order.id, "   ", expected_version=1, unit_of_work=f.unit_of_work, clock=f.clock
        )

    assert f.database.orders[order.id].status is OrderStatus.OPEN
