"""Northfield's committed catalog (ADR-0025): the products the demo seed loads."""

import uuid
from importlib import resources

import pytest

from pricewright.demo.catalog import (
    COLUMNS,
    CatalogFormatError,
    CatalogItem,
    format_catalog,
    northfield_catalog,
    parse_catalog,
)
from pricewright.domain.catalog import Product
from pricewright.domain.money import Money

ROW = "Product_0001,Category_019,FST-0001,Hex Nut,Fasteners & Fixings,XBX,9.50,5.25\n"


def _product(item: CatalogItem) -> Product:
    return Product.create(
        tenant_id=uuid.uuid7(),
        currency="USD",
        sku=item.sku,
        name=item.name,
        unit=item.unit,
        list_price=Money(item.list_price, "USD"),
        unit_cost=Money(item.unit_cost, "USD"),
    )


def test_northfield_catalog_holds_the_top_300_products_in_8_categories() -> None:
    items = northfield_catalog()

    assert len(items) == 300
    assert len({item.category for item in items}) == 8


def test_northfield_catalog_items_are_valid_products_as_written() -> None:
    products = [(item, _product(item)) for item in northfield_catalog()]

    assert all((p.sku, p.name) == (item.sku, item.name) for item, p in products)


def test_northfield_catalog_prices_in_cents_above_cost() -> None:
    items = northfield_catalog()

    assert all(0 < item.unit_cost < item.list_price for item in items)
    assert {item.list_price.as_tuple().exponent for item in items} == {-2}
    assert {item.unit_cost.as_tuple().exponent for item in items} == {-2}


def test_northfield_catalog_codes_skus_and_names_are_unique_and_sorted_by_code() -> None:
    items = northfield_catalog()
    codes = [item.dataset_code for item in items]

    assert codes == sorted(set(codes))
    assert len({item.sku.casefold() for item in items}) == len(items)
    assert len({item.name.casefold() for item in items}) == len(items)


def test_northfield_catalog_names_each_dataset_category_once() -> None:
    pairs = {(item.dataset_category, item.category) for item in northfield_catalog()}

    assert len(pairs) == len({code for code, _ in pairs}) == len({name for _, name in pairs})


def test_format_catalog_writes_the_committed_file_back_byte_for_byte() -> None:
    path = resources.files("pricewright.demo").joinpath("data/northfield_catalog.csv")
    text = path.read_text(encoding="utf-8")

    assert format_catalog(parse_catalog(text)) == text


def test_parse_catalog_reads_a_row_into_typed_values() -> None:
    (item,) = parse_catalog(",".join(COLUMNS) + "\n" + ROW)

    assert item == CatalogItem(
        dataset_code="Product_0001",
        dataset_category="Category_019",
        sku="FST-0001",
        name="Hex Nut",
        category="Fasteners & Fixings",
        unit=_product(item).unit,
        list_price=item.list_price,
        unit_cost=item.unit_cost,
    )
    assert (str(item.list_price), str(item.unit_cost), item.unit.value) == ("9.50", "5.25", "XBX")


def test_parse_catalog_refuses_any_other_column_such_as_demand() -> None:
    with pytest.raises(CatalogFormatError, match="expected the columns"):
        parse_catalog(",".join([*COLUMNS, "Order_Demand"]) + "\n" + ROW.rstrip() + ",100\n")


@pytest.mark.parametrize(
    ("old", "new"), [("Product_0001", "FST-0001"), ("Category_019", "Fasteners")]
)
def test_parse_catalog_refuses_codes_that_are_not_the_datasets(old: str, new: str) -> None:
    with pytest.raises(CatalogFormatError, match="not a dataset"):
        parse_catalog(",".join(COLUMNS) + "\n" + ROW.replace(old, new, 1))
