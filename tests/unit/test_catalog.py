import uuid
from decimal import Decimal

import pytest

from pricewright.domain.catalog import (
    KEEP,
    MAX_CATEGORY_NAME_LENGTH,
    InvalidCatalogError,
    Product,
    ProductCategory,
    UnitOfMeasure,
)
from pricewright.domain.money import Money

TENANT_ID = uuid.uuid7()
CATEGORY_ID = uuid.uuid7()


def test_category_create_trims_the_name_and_assigns_a_uuid7() -> None:
    category = ProductCategory.create(tenant_id=TENANT_ID, name="  Fasteners ")

    assert (category.name, category.tenant_id, category.version) == ("Fasteners", TENANT_ID, 1)
    assert category.id.version == 7


@pytest.mark.parametrize("name", ["", "   ", "x" * (MAX_CATEGORY_NAME_LENGTH + 1)])
def test_category_name_needs_1_to_100_characters(name: str) -> None:
    with pytest.raises(InvalidCatalogError, match="1 to 100"):
        ProductCategory.create(tenant_id=TENANT_ID, name=name)


def test_category_rename_validates_the_new_name() -> None:
    category = ProductCategory.create(tenant_id=TENANT_ID, name="Fasteners")

    category.rename(" Screws and bolts ")
    with pytest.raises(InvalidCatalogError):
        category.rename(" ")

    assert category.name == "Screws and bolts"


def usd(amount: str) -> Money:
    return Money(Decimal(amount), "USD")


def new_product(
    *,
    sku: str = " FAS-M6-100 ",
    name: str = " Hex bolt M6 x 100 ",
    list_price: Money | None = None,
    unit_cost: Money | None = None,
    category_id: uuid.UUID | None = None,
) -> Product:
    return Product.create(
        tenant_id=TENANT_ID,
        currency="USD",
        sku=sku,
        name=name,
        unit=UnitOfMeasure.BOX,
        list_price=list_price or usd("12.5"),
        unit_cost=unit_cost or usd("7.25"),
        category_id=category_id,
    )


def test_product_create_trims_the_sku_and_name_and_keeps_the_prices() -> None:
    product = new_product()

    assert (product.sku, product.name) == ("FAS-M6-100", "Hex bolt M6 x 100")
    assert (product.list_price, product.unit_cost, product.currency) == (
        usd("12.5"),
        usd("7.25"),
        "USD",
    )
    assert (product.is_active, product.category_id, product.version) == (True, None, 1)
    assert product.id.version == 7


@pytest.mark.parametrize(
    "sku", ["", "-starts-with-dash", "has space", "x" * 41, "semi;colon"], ids=str
)
def test_product_sku_is_a_code_of_1_to_40_characters(sku: str) -> None:
    with pytest.raises(InvalidCatalogError, match="SKU"):
        new_product(sku=sku)


@pytest.mark.parametrize("sku", ["Product_0993", "A", "PEN/BLU.10-PK", "x" * 40])
def test_product_sku_accepts_codes_from_other_systems(sku: str) -> None:
    assert new_product(sku=sku).sku == sku


@pytest.mark.parametrize("name", ["", "  ", "x" * 201], ids=["empty", "blank", "too-long"])
def test_product_name_has_1_to_200_characters(name: str) -> None:
    with pytest.raises(InvalidCatalogError, match="product name"):
        new_product(name=name)


EUR = Money(Decimal(1), "EUR")
NEGATIVE = Money(Decimal("-0.01"), "USD")


@pytest.mark.parametrize(
    ("list_price", "unit_cost", "problem"),
    [
        (EUR, None, "list_price must be in USD"),
        (None, EUR, "unit_cost must be in USD"),
        (NEGATIVE, None, "list_price cannot be negative"),
        (None, NEGATIVE, "unit_cost cannot be negative"),
    ],
)
def test_product_prices_are_in_the_tenants_currency_and_not_negative(
    list_price: Money | None, unit_cost: Money | None, problem: str
) -> None:
    with pytest.raises(InvalidCatalogError, match=problem):
        new_product(list_price=list_price, unit_cost=unit_cost)


def test_product_below_cost_is_allowed() -> None:
    product = new_product(list_price=usd("5"), unit_cost=usd("7.25"))

    assert product.list_price.amount < product.unit_cost.amount


def test_product_change_keeps_what_is_not_sent() -> None:
    product = new_product(category_id=CATEGORY_ID)

    product.change(list_price=usd("13"), is_active=False)

    assert (product.list_price, product.unit_cost) == (usd("13"), usd("7.25"))
    assert (product.is_active, product.category_id, product.unit) == (
        False,
        CATEGORY_ID,
        UnitOfMeasure.BOX,
    )


def test_product_change_can_clear_the_category() -> None:
    product = new_product(category_id=CATEGORY_ID)

    product.change(category_id=None)
    product.change(name="Hex bolt M6", category_id=KEEP)

    assert (product.category_id, product.name) == (None, "Hex bolt M6")


def test_product_change_is_all_or_nothing() -> None:
    product = new_product()

    with pytest.raises(InvalidCatalogError):
        product.change(name="Renamed", unit_cost=EUR)

    assert product.name == "Hex bolt M6 x 100"
