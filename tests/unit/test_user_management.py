import uuid
from decimal import Decimal

import pytest

from pricewright.application.ports import UnitOfWork
from pricewright.application.users import (
    NewUser,
    UserChanges,
    change_user,
    create_user,
    get_user,
    list_users,
    unlock_user,
)
from pricewright.domain.audit import AuditAction
from pricewright.domain.auth import PermissionDeniedError, Principal
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import (
    MAX_FAILED_LOGINS,
    EmailAlreadyRegisteredError,
    LastAdminError,
    Role,
    User,
    WeakPasswordError,
)
from tests.fakes import FakeClock, FakePasswordHasher, FakeUnitOfWork, InMemoryDatabase

CLOCK = FakeClock()


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

    blair = (
        await create_user(
            caller(fixture.northfield),
            BLAIR,
            unit_of_work=fixture.unit_of_work,
            hasher=FakePasswordHasher(),
            clock=CLOCK,
        )
    ).value

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
            clock=CLOCK,
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
            clock=CLOCK,
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
            clock=CLOCK,
        )


def test_new_user_never_shows_the_password() -> None:
    assert BLAIR.password not in repr(BLAIR)


def add_admin(fixture: Fixture, email: str) -> User:
    admin = User.create(
        tenant_id=fixture.northfield.id,
        email=email,
        full_name="Admin",
        role=Role.ADMIN,
        password_hash="hash",
    )
    fixture.database.users[admin.id] = admin
    return admin


async def change(fixture: Fixture, user: User, changes: UserChanges, version: int = 1) -> User:
    return await change_user(
        caller(fixture.northfield),
        user.id,
        changes,
        expected_version=version,
        unit_of_work=fixture.unit_of_work,
        clock=CLOCK,
    )


async def test_change_user_saves_a_new_version() -> None:
    fixture = Fixture(reps=1)
    [rep] = fixture.reps

    changed = await change(fixture, rep, UserChanges(role=Role.SALES_MANAGER))

    assert (changed.role, changed.version) == (Role.SALES_MANAGER, 2)
    assert fixture.database.users[rep.id].role is Role.SALES_MANAGER


async def test_change_user_based_on_an_old_version_is_rejected() -> None:
    fixture = Fixture(reps=1)
    [rep] = fixture.reps
    await change(fixture, rep, UserChanges(full_name="First edit"))

    with pytest.raises(StaleVersionError):
        await change(fixture, rep, UserChanges(full_name="Second edit"), version=1)


@pytest.mark.parametrize(
    "changes",
    [UserChanges(role=Role.SALES_MANAGER), UserChanges(is_active=False)],
    ids=["demote", "deactivate"],
)
async def test_the_last_active_admin_cannot_be_demoted_or_deactivated(
    changes: UserChanges,
) -> None:
    fixture = Fixture(reps=0)
    only_admin = add_admin(fixture, "avery@northfield.example")

    with pytest.raises(LastAdminError):
        await change(fixture, only_admin, changes)

    assert fixture.database.users[only_admin.id].is_active_admin


async def test_an_admin_can_be_demoted_while_another_remains() -> None:
    fixture = Fixture(reps=0)
    avery = add_admin(fixture, "avery@northfield.example")
    add_admin(fixture, "blair@northfield.example")

    demoted = await change(fixture, avery, UserChanges(role=Role.SALES_REP))

    assert demoted.role is Role.SALES_REP


async def test_change_user_of_another_tenant_or_by_a_non_admin_fails() -> None:
    fixture = Fixture(reps=1)
    [rep] = fixture.reps

    with pytest.raises(NotFoundError):
        await change_user(
            caller(fixture.larkspur),
            rep.id,
            UserChanges(is_active=False),
            expected_version=1,
            unit_of_work=fixture.unit_of_work,
            clock=CLOCK,
        )
    with pytest.raises(PermissionDeniedError):
        await change_user(
            caller(fixture.northfield, Role.SALES_MANAGER),
            rep.id,
            UserChanges(is_active=False),
            expected_version=1,
            unit_of_work=fixture.unit_of_work,
            clock=CLOCK,
        )


async def test_unlock_user_clears_the_lockout() -> None:
    fixture = Fixture(reps=1)
    [rep] = fixture.reps
    rep.failed_login_attempts = MAX_FAILED_LOGINS

    unlocked = await unlock_user(
        caller(fixture.northfield), rep.id, unit_of_work=fixture.unit_of_work, clock=CLOCK
    )

    assert not unlocked.is_locked
    assert fixture.database.users[rep.id].failed_login_attempts == 0


async def test_unlock_user_of_another_tenant_is_not_found() -> None:
    fixture = Fixture(reps=1)

    with pytest.raises(NotFoundError):
        await unlock_user(
            caller(fixture.larkspur),
            fixture.reps[0].id,
            unit_of_work=fixture.unit_of_work,
            clock=CLOCK,
        )


async def test_create_user_records_the_new_user_without_the_password() -> None:
    fixture = Fixture(reps=0)
    admin = caller(fixture.northfield)

    blair = (
        await create_user(
            admin,
            BLAIR,
            unit_of_work=fixture.unit_of_work,
            hasher=FakePasswordHasher(),
            clock=CLOCK,
        )
    ).value

    [event] = fixture.database.audit_events.values()
    assert (event.action, event.actor_id, event.resource_id) == (
        AuditAction.USER_CREATED,
        admin.subject_id,
        blair.id,
    )
    assert event.changes == {
        "email": (None, "blair@northfield.example"),
        "full_name": (None, "Blair Manager"),
        "role": (None, "sales_manager"),
        "is_active": (None, True),
        "locked": (None, False),
    }
    assert BLAIR.password not in repr(event)


async def test_change_user_records_only_what_changed() -> None:
    fixture = Fixture(reps=1)
    [rep] = fixture.reps

    await change(fixture, rep, UserChanges(full_name="Rep 0", role=Role.SALES_MANAGER))

    [event] = fixture.database.audit_events.values()
    assert event.action is AuditAction.USER_UPDATED
    assert event.changes == {"role": ("sales_rep", "sales_manager")}


async def test_refused_change_records_nothing() -> None:
    fixture = Fixture(reps=0)
    only_admin = add_admin(fixture, "avery@northfield.example")

    with pytest.raises(LastAdminError):
        await change(fixture, only_admin, UserChanges(is_active=False))

    assert fixture.database.audit_events == {}


async def test_unlock_user_records_the_unlock_only_when_the_user_was_locked() -> None:
    fixture = Fixture(reps=2)
    locked, free = fixture.reps
    locked.failed_login_attempts = MAX_FAILED_LOGINS

    for user in (locked, free):
        await unlock_user(
            caller(fixture.northfield), user.id, unit_of_work=fixture.unit_of_work, clock=CLOCK
        )

    [event] = fixture.database.audit_events.values()
    assert (event.action, event.resource_id) == (AuditAction.USER_UNLOCKED, locked.id)
    assert event.changes == {"locked": (True, False)}
