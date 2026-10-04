import uuid
from decimal import Decimal

import pytest

from pricewright.application.catalog import (
    NewProduct,
    ProductChanges,
    change_product,
    create_product,
    get_product,
    list_products,
)
from pricewright.application.pagination import Keyset
from pricewright.application.ports import ProductQuery, ProductSort, UnitOfWork
from pricewright.domain.audit import AuditAction
from pricewright.domain.auth import Permission, PermissionDeniedError, Principal
from pricewright.domain.catalog import (
    InvalidCatalogError,
    Product,
    ProductCategory,
    SkuTakenError,
    UnitOfMeasure,
    UnknownCategoryError,
)
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.money import Money
from pricewright.domain.tenants import Tenant, TenantSettings
from pricewright.domain.users import Role
from tests.fakes import FakeClock, FakeUnitOfWork, InMemoryDatabase


def usd(amount: str) -> Money:
    return Money(Decimal(amount), "USD")


def bolts(
    *,
    sku: str = "FAS-M6-100",
    name: str = "Hex bolt M6 x 100",
    category_id: uuid.UUID | None = None,
) -> NewProduct:
    return NewProduct(
        sku=sku,
        name=name,
        unit=UnitOfMeasure.BOX,
        list_price=usd("12.50"),
        unit_cost=usd("7.25"),
        category_id=category_id,
    )


class Fixture:
    def __init__(self) -> None:
        self.northfield = Tenant.register(
            name="Northfield", settings=TenantSettings("USD", Decimal(0))
        )
        self.larkspur = Tenant.register(name="Larkspur", settings=TenantSettings("EUR", Decimal(0)))
        self.fasteners = ProductCategory.create(tenant_id=self.northfield.id, name="Fasteners")
        self.their_category = ProductCategory.create(tenant_id=self.larkspur.id, name="Tools")
        self.database = InMemoryDatabase(
            tenants={self.northfield.id: self.northfield, self.larkspur.id: self.larkspur},
            product_categories={
                category.id: category for category in (self.fasteners, self.their_category)
            },
        )
        self.clock = FakeClock()
        self.admin = Principal(self.northfield.id, uuid.uuid7(), Role.ADMIN)

    def unit_of_work(self) -> UnitOfWork:
        return FakeUnitOfWork(self.database)

    async def add(self, new: NewProduct) -> Product:
        return await create_product(
            self.admin, new, unit_of_work=self.unit_of_work, clock=self.clock
        )

    async def change(self, product: Product, changes: ProductChanges, version: int = 1) -> Product:
        return await change_product(
            self.admin,
            product.id,
            changes,
            expected_version=version,
            unit_of_work=self.unit_of_work,
            clock=self.clock,
        )


async def test_an_admin_adds_a_product_and_the_trail_records_it() -> None:
    fixture = Fixture()

    product = await fixture.add(bolts(category_id=fixture.fasteners.id))

    assert fixture.database.products[product.id] == product
    [event] = fixture.database.audit_events.values()
    assert (event.action, event.resource_id) == (AuditAction.PRODUCT_CREATED, product.id)
    assert event.changes == {
        "sku": (None, "FAS-M6-100"),
        "name": (None, "Hex bolt M6 x 100"),
        "category_id": (None, str(fixture.fasteners.id)),
        "unit": (None, "XBX"),
        "list_price": (None, "12.5000"),
        "unit_cost": (None, "7.2500"),
        "is_active": (None, True),
    }


async def test_prices_follow_the_tenants_currency() -> None:
    fixture = Fixture()
    larkspur_admin = Principal(fixture.larkspur.id, uuid.uuid7(), Role.ADMIN)

    with pytest.raises(InvalidCatalogError, match="EUR"):
        await create_product(
            larkspur_admin, bolts(), unit_of_work=fixture.unit_of_work, clock=fixture.clock
        )


async def test_skus_are_unique_ignoring_case() -> None:
    fixture = Fixture()
    await fixture.add(bolts())

    with pytest.raises(SkuTakenError, match="FAS-M6-100"):
        await fixture.add(bolts(sku="fas-m6-100"))


@pytest.mark.parametrize("which", ["missing", "another tenant's"])
async def test_a_product_needs_a_category_of_its_tenant(which: str) -> None:
    fixture = Fixture()
    category_id = uuid.uuid7() if which == "missing" else fixture.their_category.id

    with pytest.raises(UnknownCategoryError):
        await fixture.add(bolts(category_id=category_id))


async def test_products_are_listed_by_the_requested_order_and_filters() -> None:
    fixture = Fixture()
    for sku, name in [("TAPE-48", "Packing tape"), ("BOLT-M6", "Hex bolt"), ("NUT-M6", "Hex nut")]:
        await fixture.add(bolts(sku=sku, name=name))
    seen: list[str] = []
    after: Keyset | None = None
    query = ProductQuery(text="hex", sort=ProductSort.SKU, descending=True)

    while True:
        page = await list_products(
            fixture.admin, query, after=after, limit=1, unit_of_work=fixture.unit_of_work
        )
        seen += [product.sku for product in page.items]
        if page.next_after is None:
            break
        after = page.next_after

    assert seen == ["NUT-M6", "BOLT-M6"]


@pytest.mark.parametrize("sort", list(ProductSort))
async def test_a_page_position_holds_the_sort_value(sort: ProductSort) -> None:
    fixture = Fixture()
    first = await fixture.add(bolts(sku="A-1", name="Anchors"))
    await fixture.add(bolts(sku="B-2", name="Bolts"))

    page = await list_products(
        fixture.admin,
        ProductQuery(sort=sort),
        after=None,
        limit=1,
        unit_of_work=fixture.unit_of_work,
    )

    expected = {ProductSort.SKU: "A-1", ProductSort.NAME: "Anchors", ProductSort.CREATED: None}
    assert page.next_after == Keyset(first.id, expected[sort])


async def test_a_product_is_edited_with_a_new_version_and_an_audit_event() -> None:
    fixture = Fixture()
    product = await fixture.add(bolts(category_id=fixture.fasteners.id))

    changed = await fixture.change(
        product, ProductChanges(list_price=usd("13"), category_id=None, name="Hex bolt M6 x 100")
    )

    assert (changed.version, changed.list_price, changed.category_id) == (2, usd("13"), None)
    _, edit = sorted(fixture.database.audit_events.values(), key=lambda event: event.id)
    assert edit.action is AuditAction.PRODUCT_UPDATED
    assert edit.changes == {
        "list_price": ("12.5000", "13.0000"),
        "category_id": (str(fixture.fasteners.id), None),
    }


async def test_a_price_written_with_another_scale_is_no_change() -> None:
    fixture = Fixture()
    product = await fixture.add(bolts())  # 12.50, as a client sent it

    await fixture.change(product, ProductChanges(list_price=usd("12.5000")))

    assert [event.action for event in fixture.database.audit_events.values()] == [
        AuditAction.PRODUCT_CREATED
    ]


async def test_archiving_a_product_keeps_it_readable() -> None:
    fixture = Fixture()
    product = await fixture.add(bolts())

    await fixture.change(product, ProductChanges(is_active=False))
    archived = await get_product(fixture.admin, product.id, unit_of_work=fixture.unit_of_work)
    page = await list_products(
        fixture.admin,
        ProductQuery(active=True),
        after=None,
        limit=10,
        unit_of_work=fixture.unit_of_work,
    )

    assert not archived.is_active
    assert page.items == []


async def test_editing_from_an_old_version_is_rejected() -> None:
    fixture = Fixture()
    product = await fixture.add(bolts())
    await fixture.change(product, ProductChanges(name="Hex bolt"))

    with pytest.raises(StaleVersionError):
        await fixture.change(product, ProductChanges(name="Bolt"), version=1)


async def test_moving_a_product_to_another_tenants_category_is_refused() -> None:
    fixture = Fixture()
    product = await fixture.add(bolts())

    with pytest.raises(UnknownCategoryError):
        await fixture.change(product, ProductChanges(category_id=fixture.their_category.id))


@pytest.mark.parametrize("role", [Role.SALES_REP, Role.SALES_MANAGER])
async def test_reps_and_managers_read_the_catalog_but_do_not_change_it(role: Role) -> None:
    fixture = Fixture()
    product = await fixture.add(bolts())
    caller = Principal(fixture.northfield.id, uuid.uuid7(), role)

    found = await get_product(caller, product.id, unit_of_work=fixture.unit_of_work)
    with pytest.raises(PermissionDeniedError, match="catalog:manage"):
        await create_product(
            caller, bolts(sku="X-1"), unit_of_work=fixture.unit_of_work, clock=fixture.clock
        )

    assert found == product


async def test_an_integration_with_catalog_read_finds_a_product_by_sku() -> None:
    fixture = Fixture()
    product = await fixture.add(bolts())
    integration = Principal(
        fixture.northfield.id, uuid.uuid7(), scopes=frozenset({Permission.CATALOG_READ})
    )

    page = await list_products(
        integration,
        ProductQuery(sku="fas-m6-100"),
        after=None,
        limit=10,
        unit_of_work=fixture.unit_of_work,
    )

    assert page.items == [product]


async def test_another_tenants_product_is_not_found() -> None:
    fixture = Fixture()
    product = await fixture.add(bolts())
    outsider = Principal(fixture.larkspur.id, uuid.uuid7(), Role.ADMIN)

    with pytest.raises(NotFoundError):
        await get_product(outsider, product.id, unit_of_work=fixture.unit_of_work)
    with pytest.raises(NotFoundError):
        await change_product(
            outsider,
            product.id,
            ProductChanges(is_active=False),
            expected_version=1,
            unit_of_work=fixture.unit_of_work,
            clock=fixture.clock,
        )


async def test_a_tenant_that_no_longer_exists_cannot_add_products() -> None:
    fixture = Fixture()
    ghost = Principal(uuid.uuid7(), uuid.uuid7(), Role.ADMIN)

    with pytest.raises(NotFoundError):
        await create_product(ghost, bolts(), unit_of_work=fixture.unit_of_work, clock=fixture.clock)
