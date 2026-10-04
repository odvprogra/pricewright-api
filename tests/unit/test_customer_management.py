import uuid
from decimal import Decimal

import pytest

from pricewright.application.customers import (
    CustomerChanges,
    NewCustomer,
    change_customer,
    create_customer,
    get_customer,
    list_customers,
)
from pricewright.application.pagination import Keyset
from pricewright.application.ports import CustomerQuery, CustomerSort, UnitOfWork
from pricewright.domain.audit import AuditAction
from pricewright.domain.auth import Permission, PermissionDeniedError, Principal
from pricewright.domain.customers import AccountNumberTakenError, Customer, CustomerTier
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeClock, FakeUnitOfWork, InMemoryDatabase

ACME = NewCustomer(
    account_number="C-1001",
    name="Acme Industrial",
    tier=CustomerTier.GOLD,
    payment_terms_days=60,
    tax_id="de 123.456-789",
)


class Fixture:
    def __init__(self) -> None:
        settings = TenantSettings("USD", Decimal(0))
        self.northfield = Tenant.register(name="Northfield", settings=settings)
        self.larkspur = Tenant.register(name="Larkspur", settings=settings)
        self.database = InMemoryDatabase(
            tenants={self.northfield.id: self.northfield, self.larkspur.id: self.larkspur}
        )
        self.clock = FakeClock()
        self.rep = Principal(self.northfield.id, uuid.uuid7(), Role.SALES_REP)

    def unit_of_work(self) -> UnitOfWork:
        return FakeUnitOfWork(self.database)

    async def add(self, new: NewCustomer = ACME) -> Customer:
        return await create_customer(
            self.rep, new, unit_of_work=self.unit_of_work, clock=self.clock
        )

    async def change(
        self, customer: Customer, changes: CustomerChanges, version: int = 1
    ) -> Customer:
        return await change_customer(
            self.rep,
            customer.id,
            changes,
            expected_version=version,
            unit_of_work=self.unit_of_work,
            clock=self.clock,
        )


async def test_a_rep_adds_a_customer_and_the_trail_records_it() -> None:
    fixture = Fixture()

    customer = await fixture.add()

    assert fixture.database.customers[customer.id] == customer
    [event] = fixture.database.audit_events.values()
    assert (event.action, event.actor_id) == (AuditAction.CUSTOMER_CREATED, fixture.rep.subject_id)
    assert event.changes == {
        "account_number": (None, "C-1001"),
        "name": (None, "Acme Industrial"),
        "tax_id": (None, "DE123456789"),
        "tier": (None, "gold"),
        "payment_terms_days": (None, 60),
        "is_active": (None, True),
    }


async def test_account_numbers_are_unique_ignoring_case() -> None:
    fixture = Fixture()
    await fixture.add()

    with pytest.raises(AccountNumberTakenError, match="C-1001"):
        await fixture.add(NewCustomer(account_number="c-1001", name="Other"))


async def test_customers_are_listed_by_the_requested_order_and_filters() -> None:
    fixture = Fixture()
    for number, name, tier in [
        ("C-3", "Zenith Tools", CustomerTier.GOLD),
        ("C-1", "Acme Industrial", CustomerTier.GOLD),
        ("C-2", "Bolt & Co", CustomerTier.SILVER),
    ]:
        await fixture.add(NewCustomer(account_number=number, name=name, tier=tier))
    seen: list[str] = []
    after: Keyset | None = None
    query = CustomerQuery(tier=CustomerTier.GOLD, sort=CustomerSort.NAME, descending=True)

    while True:
        page = await list_customers(
            fixture.rep, query, after=after, limit=1, unit_of_work=fixture.unit_of_work
        )
        seen += [customer.name for customer in page.items]
        if page.next_after is None:
            break
        after = page.next_after

    assert seen == ["Zenith Tools", "Acme Industrial"]


@pytest.mark.parametrize("sort", list(CustomerSort))
async def test_a_page_position_holds_the_sort_value(sort: CustomerSort) -> None:
    fixture = Fixture()
    first = await fixture.add(NewCustomer(account_number="A-1", name="Acme"))
    await fixture.add(NewCustomer(account_number="B-2", name="Bolt"))

    page = await list_customers(
        fixture.rep,
        CustomerQuery(sort=sort),
        after=None,
        limit=1,
        unit_of_work=fixture.unit_of_work,
    )

    expected = {
        CustomerSort.ACCOUNT_NUMBER: "A-1",
        CustomerSort.NAME: "Acme",
        CustomerSort.CREATED: None,
    }
    assert page.next_after == Keyset(first.id, expected[sort])


async def test_a_customer_is_edited_with_a_new_version_and_an_audit_event() -> None:
    fixture = Fixture()
    customer = await fixture.add()

    edited = await fixture.change(
        customer, CustomerChanges(tier=CustomerTier.SILVER, tax_id=None, name="Acme Industrial")
    )

    assert (edited.version, edited.tier, edited.tax_id) == (2, CustomerTier.SILVER, None)
    _, edit = sorted(fixture.database.audit_events.values(), key=lambda event: event.id)
    assert edit.action is AuditAction.CUSTOMER_UPDATED
    assert edit.changes == {"tax_id": ("DE123456789", None), "tier": ("gold", "silver")}


async def test_archiving_a_customer_keeps_it_readable() -> None:
    fixture = Fixture()
    customer = await fixture.add()

    await fixture.change(customer, CustomerChanges(is_active=False))
    archived = await get_customer(fixture.rep, customer.id, unit_of_work=fixture.unit_of_work)
    active = await list_customers(
        fixture.rep,
        CustomerQuery(active=True),
        after=None,
        limit=10,
        unit_of_work=fixture.unit_of_work,
    )

    assert not archived.is_active
    assert active.items == []


async def test_editing_from_an_old_version_is_rejected() -> None:
    fixture = Fixture()
    customer = await fixture.add()
    await fixture.change(customer, CustomerChanges(payment_terms_days=45))

    with pytest.raises(StaleVersionError):
        await fixture.change(customer, CustomerChanges(payment_terms_days=15), version=1)


async def test_an_integration_needs_the_customer_scopes() -> None:
    fixture = Fixture()
    reader = Principal(
        fixture.northfield.id, uuid.uuid7(), scopes=frozenset({Permission.CUSTOMERS_READ})
    )
    await fixture.add()

    page = await list_customers(
        reader,
        CustomerQuery(tax_id="DE123456789"),
        after=None,
        limit=10,
        unit_of_work=fixture.unit_of_work,
    )
    with pytest.raises(PermissionDeniedError, match="customers:manage"):
        await create_customer(
            reader,
            NewCustomer(account_number="C-2", name="Bolt"),
            unit_of_work=fixture.unit_of_work,
            clock=fixture.clock,
        )

    assert [customer.account_number for customer in page.items] == ["C-1001"]


async def test_another_tenants_customer_is_not_found() -> None:
    fixture = Fixture()
    customer = await fixture.add()
    outsider = Principal(fixture.larkspur.id, uuid.uuid7(), Role.ADMIN)

    with pytest.raises(NotFoundError):
        await get_customer(outsider, customer.id, unit_of_work=fixture.unit_of_work)
    with pytest.raises(NotFoundError):
        await change_customer(
            outsider,
            customer.id,
            CustomerChanges(tier=CustomerTier.GOLD),
            expected_version=1,
            unit_of_work=fixture.unit_of_work,
            clock=fixture.clock,
        )
