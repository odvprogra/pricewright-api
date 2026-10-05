import dataclasses
from decimal import Decimal

import pytest

from pricewright.application.onboarding import (
    RegisteredTenant,
    RegisterTenant,
    register_tenant,
)
from pricewright.application.ports import UnitOfWork
from pricewright.domain.tenants import InvalidTenantError
from pricewright.domain.users import EmailAlreadyRegisteredError, Role, WeakPasswordError
from tests.fakes import FakePasswordHasher, FakeUnitOfWork, InMemoryDatabase

PASSWORD = "northfield admin passphrase"
NORTHFIELD = RegisterTenant(
    name="Northfield Supply",
    currency="USD",
    tax_rate=Decimal("0.07"),
    admin_email="Avery@Northfield.Example",
    admin_full_name="Avery Admin",
    admin_password=PASSWORD,
)


async def register(
    database: InMemoryDatabase, command: RegisterTenant = NORTHFIELD
) -> RegisteredTenant:
    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(database)

    return await register_tenant(command, unit_of_work=unit_of_work, hasher=FakePasswordHasher())


async def test_register_tenant_numbers_quotes_with_the_given_prefix() -> None:
    database = InMemoryDatabase()
    command = dataclasses.replace(NORTHFIELD, quote_prefix="NF", quote_validity_days=45)

    registered = await register(database, command)

    settings = database.tenants[registered.tenant_id].settings
    assert (settings.quote_prefix, settings.quote_validity_days) == ("NF", 45)


async def test_register_tenant_creates_the_tenant_and_its_admin() -> None:
    database = InMemoryDatabase()

    registered = await register(database)

    tenant = database.tenants[registered.tenant_id]
    admin = database.users[registered.admin_id]
    assert (tenant.name, tenant.settings.currency, tenant.settings.tax_rate) == (
        "Northfield Supply",
        "USD",
        Decimal("0.07"),
    )
    assert (admin.tenant_id, admin.email, admin.role) == (
        tenant.id,
        "avery@northfield.example",
        Role.ADMIN,
    )
    assert admin.password_hash == FakePasswordHasher.PREFIX + PASSWORD


async def test_register_tenant_refuses_an_email_registered_in_any_tenant() -> None:
    database = InMemoryDatabase()
    await register(database)

    larkspur = dataclasses.replace(
        NORTHFIELD, name="Larkspur Tool Co.", admin_email="AVERY@northfield.example"
    )

    with pytest.raises(EmailAlreadyRegisteredError, match=r"avery@northfield\.example"):
        await register(database, larkspur)

    assert len(database.tenants) == 1


@pytest.mark.parametrize(
    ("command", "error"),
    [
        (dataclasses.replace(NORTHFIELD, admin_password="too short"), WeakPasswordError),
        (dataclasses.replace(NORTHFIELD, tax_rate=Decimal("1.5")), InvalidTenantError),
        (dataclasses.replace(NORTHFIELD, currency="dollars"), InvalidTenantError),
    ],
    ids=["weak-password", "tax-rate", "currency"],
)
async def test_register_tenant_with_invalid_input_saves_nothing(
    command: RegisterTenant, error: type[Exception]
) -> None:
    database = InMemoryDatabase()

    with pytest.raises(error):
        await register(database, command)

    assert database == InMemoryDatabase()


def test_register_tenant_command_never_shows_the_password() -> None:
    assert PASSWORD not in repr(NORTHFIELD)
