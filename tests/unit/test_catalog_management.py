import uuid
from decimal import Decimal

import pytest

from pricewright.application.catalog import (
    create_category,
    get_category,
    list_categories,
    rename_category,
)
from pricewright.application.pagination import Keyset
from pricewright.application.ports import UnitOfWork
from pricewright.domain.audit import AuditAction
from pricewright.domain.auth import Permission, PermissionDeniedError, Principal
from pricewright.domain.catalog import CategoryNameTakenError, ProductCategory
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeClock, FakeUnitOfWork, InMemoryDatabase


class Fixture:
    def __init__(self) -> None:
        settings = TenantSettings("USD", Decimal(0))
        self.northfield = Tenant.register(name="Northfield", settings=settings)
        self.larkspur = Tenant.register(name="Larkspur", settings=settings)
        self.database = InMemoryDatabase(
            tenants={self.northfield.id: self.northfield, self.larkspur.id: self.larkspur}
        )
        self.clock = FakeClock()
        self.admin = Principal(self.northfield.id, uuid.uuid7(), Role.ADMIN)

    def unit_of_work(self) -> UnitOfWork:
        return FakeUnitOfWork(self.database)

    async def add(self, name: str) -> ProductCategory:
        return await create_category(
            self.admin, name=name, unit_of_work=self.unit_of_work, clock=self.clock
        )

    async def rename(
        self, category: ProductCategory, name: str, version: int = 1
    ) -> ProductCategory:
        return await rename_category(
            self.admin,
            category.id,
            name=name,
            expected_version=version,
            unit_of_work=self.unit_of_work,
            clock=self.clock,
        )


async def test_an_admin_adds_a_category_and_the_trail_records_it() -> None:
    fixture = Fixture()

    category = await fixture.add("Fasteners")

    assert fixture.database.product_categories[category.id].name == "Fasteners"
    [event] = fixture.database.audit_events.values()
    assert (event.action, event.resource_id) == (AuditAction.PRODUCT_CATEGORY_CREATED, category.id)
    assert event.changes == {"name": (None, "Fasteners")}


async def test_category_names_are_unique_ignoring_case() -> None:
    fixture = Fixture()
    await fixture.add("Fasteners")

    with pytest.raises(CategoryNameTakenError, match="Fasteners"):
        await fixture.add("FASTENERS")


async def test_another_tenant_may_use_the_same_name() -> None:
    fixture = Fixture()
    await fixture.add("Fasteners")
    outsider = Principal(fixture.larkspur.id, uuid.uuid7(), Role.ADMIN)

    category = await create_category(
        outsider, name="Fasteners", unit_of_work=fixture.unit_of_work, clock=fixture.clock
    )

    assert category.tenant_id == fixture.larkspur.id


async def test_categories_are_listed_by_name_page_by_page() -> None:
    fixture = Fixture()
    for name in ["Tape", "abrasives", "Fasteners", "Gloves"]:
        await fixture.add(name)
    seen: list[str] = []
    after: Keyset | None = None

    while True:
        page = await list_categories(
            fixture.admin, after=after, limit=3, unit_of_work=fixture.unit_of_work
        )
        seen += [category.name for category in page.items]
        if page.next_after is None:
            break
        after = page.next_after

    assert seen == ["abrasives", "Fasteners", "Gloves", "Tape"]


async def test_rename_saves_a_new_version_and_records_the_change() -> None:
    fixture = Fixture()
    category = await fixture.add("Fastners")

    renamed = await fixture.rename(category, "Fasteners")

    assert (renamed.name, renamed.version) == ("Fasteners", 2)
    _, renaming = sorted(fixture.database.audit_events.values(), key=lambda event: event.id)
    assert renaming.action is AuditAction.PRODUCT_CATEGORY_UPDATED
    assert renaming.changes == {"name": ("Fastners", "Fasteners")}


async def test_rename_may_change_only_the_case_of_the_name() -> None:
    fixture = Fixture()
    category = await fixture.add("fasteners")

    renamed = await fixture.rename(category, "Fasteners")

    assert renamed.name == "Fasteners"


async def test_rename_to_another_categorys_name_is_a_conflict() -> None:
    fixture = Fixture()
    await fixture.add("Fasteners")
    tape = await fixture.add("Tape")

    with pytest.raises(CategoryNameTakenError):
        await fixture.rename(tape, "fasteners")


async def test_rename_based_on_an_old_version_is_rejected() -> None:
    fixture = Fixture()
    category = await fixture.add("Fasteners")
    await fixture.rename(category, "Screws")

    with pytest.raises(StaleVersionError):
        await fixture.rename(category, "Bolts", version=1)

    assert fixture.database.product_categories[category.id].name == "Screws"


@pytest.mark.parametrize("role", [Role.SALES_REP, Role.SALES_MANAGER])
async def test_reps_and_managers_read_categories_but_cannot_change_them(role: Role) -> None:
    fixture = Fixture()
    category = await fixture.add("Fasteners")
    caller = Principal(fixture.northfield.id, uuid.uuid7(), role)

    found = await get_category(caller, category.id, unit_of_work=fixture.unit_of_work)
    with pytest.raises(PermissionDeniedError, match="catalog:manage"):
        await create_category(
            caller, name="Tape", unit_of_work=fixture.unit_of_work, clock=fixture.clock
        )

    assert found == category


async def test_a_service_account_with_catalog_read_lists_categories() -> None:
    fixture = Fixture()
    await fixture.add("Fasteners")
    integration = Principal(
        fixture.northfield.id, uuid.uuid7(), scopes=frozenset({Permission.CATALOG_READ})
    )

    page = await list_categories(
        integration, after=None, limit=10, unit_of_work=fixture.unit_of_work
    )

    assert [category.name for category in page.items] == ["Fasteners"]


async def test_another_tenants_category_is_not_found() -> None:
    fixture = Fixture()
    category = await fixture.add("Fasteners")
    outsider = Principal(fixture.larkspur.id, uuid.uuid7(), Role.ADMIN)

    with pytest.raises(NotFoundError):
        await get_category(outsider, category.id, unit_of_work=fixture.unit_of_work)
    with pytest.raises(NotFoundError):
        await rename_category(
            outsider,
            category.id,
            name="Hijacked",
            expected_version=1,
            unit_of_work=fixture.unit_of_work,
            clock=fixture.clock,
        )
