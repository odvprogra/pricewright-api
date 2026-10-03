import uuid
from decimal import Decimal

import pytest

from pricewright.application.ports import UnitOfWork
from pricewright.application.users import NewUser, create_user, get_user, list_users
from pricewright.domain.auth import PermissionDeniedError, Principal
from pricewright.domain.errors import NotFoundError
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import EmailAlreadyRegisteredError, Role, User, WeakPasswordError
from tests.fakes import FakePasswordHasher, FakeUnitOfWork, InMemoryDatabase


class Fixture:
    """Northfield with some sales reps, and Larkspur with none."""

    def __init__(self, reps: int) -> None:
        settings = TenantSettings("USD", Decimal(0))
        self.northfield = Tenant.register(name="Northfield", settings=settings)
        self.larkspur = Tenant.register(name="Larkspur", settings=settings)
        self.reps = [
            User.create(
                tenant_id=self.northfield.id,
                email=f"rep{number}@northfield.example",
                full_name=f"Rep {number}",
                role=Role.SALES_REP,
                password_hash="hash",
            )
            for number in range(reps)
        ]
        self.database = InMemoryDatabase(
            tenants={self.northfield.id: self.northfield, self.larkspur.id: self.larkspur},
            users={rep.id: rep for rep in self.reps},
        )

    def unit_of_work(self) -> UnitOfWork:
        return FakeUnitOfWork(self.database)


def caller(tenant: Tenant, role: Role = Role.ADMIN) -> Principal:
    return Principal(tenant.id, uuid.uuid7(), role)


async def test_list_users_pages_through_every_user_in_creation_order() -> None:
    fixture = Fixture(reps=5)
    seen: list[User] = []
    after = None

    while True:
        page = await list_users(
            caller(fixture.northfield), after=after, limit=2, unit_of_work=fixture.unit_of_work
        )
        seen += page.items
        if page.next_after is None:
            break
        after = page.next_after

    assert seen == fixture.reps


async def test_list_users_last_page_has_no_continuation() -> None:
    fixture = Fixture(reps=2)

    page = await list_users(
        caller(fixture.northfield), after=None, limit=2, unit_of_work=fixture.unit_of_work
    )

    assert (page.items, page.next_after) == (fixture.reps, None)


@pytest.mark.parametrize("role", [Role.SALES_REP, Role.SALES_MANAGER])
async def test_only_admins_manage_users(role: Role) -> None:
    fixture = Fixture(reps=1)
    not_admin = caller(fixture.northfield, role)

    with pytest.raises(PermissionDeniedError):
        await list_users(not_admin, after=None, limit=10, unit_of_work=fixture.unit_of_work)
    with pytest.raises(PermissionDeniedError):
        await get_user(not_admin, fixture.reps[0].id, unit_of_work=fixture.unit_of_work)


async def test_get_user_finds_users_of_the_callers_tenant_only() -> None:
    fixture = Fixture(reps=1)
    [rep] = fixture.reps

    found = await get_user(caller(fixture.northfield), rep.id, unit_of_work=fixture.unit_of_work)

    assert found == rep
    with pytest.raises(NotFoundError):
        await get_user(caller(fixture.larkspur), rep.id, unit_of_work=fixture.unit_of_work)
    with pytest.raises(NotFoundError):
        await get_user(caller(fixture.northfield), uuid.uuid7(), unit_of_work=fixture.unit_of_work)


BLAIR = NewUser(
    email="Blair@Northfield.Example",
    full_name="Blair Manager",
    role=Role.SALES_MANAGER,
    password="blair's initial passphrase",
)


async def test_create_user_adds_a_user_to_the_callers_tenant() -> None:
    fixture = Fixture(reps=0)

    blair = await create_user(
        caller(fixture.northfield),
        BLAIR,
        unit_of_work=fixture.unit_of_work,
        hasher=FakePasswordHasher(),
    )

    stored = fixture.database.users[blair.id]
    assert (stored.tenant_id, stored.email, stored.role) == (
        fixture.northfield.id,
        "blair@northfield.example",
        Role.SALES_MANAGER,
    )
    assert stored.password_hash == FakePasswordHasher.PREFIX + BLAIR.password


async def test_create_user_refuses_an_email_registered_in_any_tenant() -> None:
    fixture = Fixture(reps=1)
    taken = NewUser(
        email="REP0@northfield.example", full_name="Copy", role=Role.SALES_REP, password="x" * 20
    )

    with pytest.raises(EmailAlreadyRegisteredError):
        await create_user(
            caller(fixture.larkspur),
            taken,
            unit_of_work=fixture.unit_of_work,
            hasher=FakePasswordHasher(),
        )


async def test_create_user_with_a_weak_password_saves_nothing() -> None:
    fixture = Fixture(reps=0)
    weak = NewUser(
        email="weak@northfield.example", full_name="Weak", role=Role.SALES_REP, password="short"
    )

    with pytest.raises(WeakPasswordError):
        await create_user(
            caller(fixture.northfield),
            weak,
            unit_of_work=fixture.unit_of_work,
            hasher=FakePasswordHasher(),
        )

    assert fixture.database.users == {}


async def test_only_admins_create_users() -> None:
    fixture = Fixture(reps=0)

    with pytest.raises(PermissionDeniedError):
        await create_user(
            caller(fixture.northfield, Role.SALES_MANAGER),
            BLAIR,
            unit_of_work=fixture.unit_of_work,
            hasher=FakePasswordHasher(),
        )


def test_new_user_never_shows_the_password() -> None:
    assert BLAIR.password not in repr(BLAIR)
