"""Customers in PostgreSQL: filters, sorts and keyset pages (ADR-0014), unique account numbers."""

import dataclasses

import pytest

from pricewright.application.pagination import Keyset
from pricewright.application.ports import CustomerQuery, CustomerSort
from pricewright.domain.customers import Customer, CustomerTier
from pricewright.domain.errors import ConflictError, StaleVersionError
from pricewright.domain.tenants import Tenant
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import Sessions, register

pytestmark = pytest.mark.integration


def customer(
    tenant: Tenant,
    account_number: str,
    name: str,
    *,
    tier: CustomerTier = CustomerTier.STANDARD,
    tax_id: str | None = None,
) -> Customer:
    return Customer.create(
        tenant_id=tenant.id, account_number=account_number, name=name, tier=tier, tax_id=tax_id
    )


async def store(sessions: Sessions, tenant: Tenant, *customers: Customer) -> None:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(tenant.id)
        for each in customers:
            await uow.customers.add(each)
        await uow.commit()


async def accounts(
    sessions: Sessions, tenant: Tenant, query: CustomerQuery, after: Keyset | None = None
) -> list[str]:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(tenant.id)
        found = await uow.customers.page(query, after=after, limit=10)
    return [each.account_number for each in found]


async def test_customers_round_trip(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    acme = dataclasses.replace(
        customer(northfield, "C-1001", "Acme", tier=CustomerTier.GOLD, tax_id="DE 123"),
        payment_terms_days=0,
    )
    await store(session_factory, northfield, acme)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        found = await uow.customers.get(acme.id)
        by_number = await uow.customers.with_account_number("c-1001")

    assert found == by_number == acme


@pytest.mark.parametrize(
    ("sort", "descending", "expected"),
    [
        (CustomerSort.NAME, False, ["C-2", "c-1", "C-3"]),
        (CustomerSort.ACCOUNT_NUMBER, True, ["C-3", "C-2", "c-1"]),
        (CustomerSort.CREATED, False, ["C-3", "c-1", "C-2"]),
    ],
)
async def test_customers_sort_and_continue_after_a_keyset(
    session_factory: Sessions, sort: CustomerSort, descending: bool, expected: list[str]
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    zenith = customer(northfield, "C-3", "zenith Tools")
    bolt = customer(northfield, "c-1", "Bolt & Co")
    acme = customer(northfield, "C-2", "Acme")
    await store(session_factory, northfield, zenith, bolt, acme)
    query = CustomerQuery(sort=sort, descending=descending)
    first = {each.account_number: each for each in (zenith, bolt, acme)}[expected[0]]
    value = {CustomerSort.NAME: first.name, CustomerSort.ACCOUNT_NUMBER: first.account_number}

    everything = await accounts(session_factory, northfield, query)
    rest = await accounts(
        session_factory, northfield, query, after=Keyset(first.id, value.get(sort))
    )

    assert everything == expected
    assert rest == expected[1:]


async def test_customers_are_filtered(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    archived = customer(northfield, "C-3", "Acme Labs")
    archived.change(is_active=False)
    await store(
        session_factory,
        northfield,
        customer(northfield, "C-1", "Acme Industrial", tier=CustomerTier.GOLD, tax_id="DE1"),
        customer(northfield, "ACME-2", "Bolt & Co", tax_id="DE1"),
        archived,
    )
    by_number = CustomerSort.ACCOUNT_NUMBER

    async def matching(query: CustomerQuery) -> list[str]:
        return await accounts(session_factory, northfield, query)

    assert await matching(CustomerQuery(text="acme", sort=by_number)) == ["ACME-2", "C-1", "C-3"]
    assert await matching(CustomerQuery(text="& co")) == ["ACME-2"]
    assert await matching(CustomerQuery(tax_id="DE1", sort=by_number)) == ["ACME-2", "C-1"]
    assert await matching(CustomerQuery(tier=CustomerTier.GOLD)) == ["C-1"]
    assert await matching(CustomerQuery(active=False)) == ["C-3"]


async def test_account_numbers_are_unique_per_tenant_ignoring_case(
    session_factory: Sessions,
) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    larkspur, _ = await register(session_factory, "Larkspur")
    await store(session_factory, northfield, customer(northfield, "C-1001", "Acme"))
    await store(session_factory, larkspur, customer(larkspur, "C-1001", "Acme"))

    with pytest.raises(ConflictError):
        await store(session_factory, northfield, customer(northfield, "c-1001", "Other"))


async def test_a_customer_is_saved_with_compare_and_set(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    acme = customer(northfield, "C-1001", "Acme")
    await store(session_factory, northfield, acme)
    stale = dataclasses.replace(acme)

    acme.change(tier=CustomerTier.SILVER, payment_terms_days=45, tax_id="FR 9")
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        await uow.customers.save(acme)
        await uow.commit()
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        with pytest.raises(StaleVersionError):
            await uow.customers.save(stale)
        stored = await uow.customers.get(acme.id)

    assert stored == acme
    assert acme.version == 2


async def test_another_tenants_customers_are_invisible(session_factory: Sessions) -> None:
    northfield, _ = await register(session_factory, "Northfield")
    larkspur, _ = await register(session_factory, "Larkspur")
    acme = customer(northfield, "C-1001", "Acme")
    await store(session_factory, northfield, acme)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(larkspur.id)
        hidden = await uow.customers.get(acme.id)
        by_number = await uow.customers.with_account_number("C-1001")
        with pytest.raises(RuntimeError):
            await uow.customers.save(acme)
        with pytest.raises(RuntimeError):
            await uow.customers.add(acme)

    assert (hidden, by_number) == (None, None)
    assert await accounts(session_factory, larkspur, CustomerQuery()) == []
