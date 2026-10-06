"""The tenant's customers: sales reps, managers and admins all manage them (brief §2)."""

from dataclasses import dataclass
from uuid import UUID

from pricewright.application.audit import customer_fields, record
from pricewright.application.idempotency import (
    Created,
    earlier_creation,
    remember_creation,
    replay,
)
from pricewright.application.pagination import Keyset, Page, page_of
from pricewright.application.ports import (
    Clock,
    CustomerQuery,
    CustomerSort,
    UnitOfWorkFactory,
)
from pricewright.domain.audit import AuditAction, AuditResourceType, changed, created
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.customers import (
    DEFAULT_PAYMENT_TERMS_DAYS,
    AccountNumberTakenError,
    Customer,
    CustomerTier,
)
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.idempotency import IdempotentRequest
from pricewright.domain.updates import KEEP, Keep


@dataclass(frozen=True, slots=True)
class NewCustomer:
    account_number: str
    name: str
    tier: CustomerTier = CustomerTier.STANDARD
    payment_terms_days: int = DEFAULT_PAYMENT_TERMS_DAYS
    tax_id: str | None = None


async def create_customer(
    principal: Principal,
    new: NewCustomer,
    *,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
    idempotency: IdempotentRequest | None = None,
) -> Created[Customer]:
    """A retry with the same ``Idempotency-Key`` gets the customer back (ADR-0022)."""
    principal.require(Permission.CUSTOMERS_MANAGE)
    now = clock()
    customer = Customer.create(
        tenant_id=principal.tenant_id,
        account_number=new.account_number,
        name=new.name,
        tier=new.tier,
        payment_terms_days=new.payment_terms_days,
        tax_id=new.tax_id,
    )
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        if (earlier := await earlier_creation(uow, principal, idempotency, now=now)) is not None:
            return await replay(uow.customers.get(earlier))
        holder = await uow.customers.with_account_number(customer.account_number)
        if holder is not None:
            raise AccountNumberTakenError(f"account {holder.account_number} already exists")
        await uow.customers.add(customer)
        changes = created(customer_fields(customer))
        action = AuditAction.CUSTOMER_CREATED
        await record(uow, principal, action, customer.id, changes, now=now)
        await remember_creation(
            uow, principal, idempotency, AuditResourceType.CUSTOMER, customer.id, now=now
        )
        await uow.commit()  # the unique index still settles a race on the account number
    return Created(customer)


def _position(customer: Customer, sort: CustomerSort) -> Keyset:
    values = {
        CustomerSort.ACCOUNT_NUMBER: customer.account_number,
        CustomerSort.NAME: customer.name,
    }
    return Keyset(customer.id, values.get(sort))


async def list_customers(
    principal: Principal,
    query: CustomerQuery,
    *,
    after: Keyset | None,
    limit: int,
    unit_of_work: UnitOfWorkFactory,
) -> Page[Customer]:
    principal.require(Permission.CUSTOMERS_READ)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        customers = await uow.customers.page(query, after=after, limit=limit + 1)
    return page_of(customers, limit, lambda customer: _position(customer, query.sort))


async def get_customer(
    principal: Principal, customer_id: UUID, *, unit_of_work: UnitOfWorkFactory
) -> Customer:
    principal.require(Permission.CUSTOMERS_READ)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        customer = await uow.customers.get(customer_id)
    if customer is None:
        raise NotFoundError("no such customer")
    return customer


@dataclass(frozen=True, slots=True)
class CustomerChanges:
    """Fields left as ``None`` keep their value; the tax id keeps it with ``KEEP``."""

    name: str | None = None
    tier: CustomerTier | None = None
    payment_terms_days: int | None = None
    tax_id: str | Keep | None = KEEP
    is_active: bool | None = None


async def change_customer(
    principal: Principal,
    customer_id: UUID,
    changes: CustomerChanges,
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> Customer:
    """Edit, archive (``is_active=False``) or restore a customer. Its account number never
    changes."""
    principal.require(Permission.CUSTOMERS_MANAGE)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        customer = await uow.customers.get(customer_id)
        if customer is None:
            raise NotFoundError("no such customer")
        if customer.version != expected_version:
            raise StaleVersionError("the customer was changed by someone else; reload it")
        before = customer_fields(customer)
        customer.change(
            name=changes.name,
            tier=changes.tier,
            payment_terms_days=changes.payment_terms_days,
            tax_id=changes.tax_id,
            is_active=changes.is_active,
        )
        await uow.customers.save(customer)
        edits = changed(before, customer_fields(customer))
        action = AuditAction.CUSTOMER_UPDATED
        await record(uow, principal, action, customer.id, edits, now=clock())
        await uow.commit()
    return customer
