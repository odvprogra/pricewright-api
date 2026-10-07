"""The script that builds Northfield's catalog from the demand dataset (ADR-0025)."""

import uuid
import zipfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pricewright.demo.catalog import format_catalog, northfield_catalog, parse_catalog
from pricewright.domain.catalog import Product
from pricewright.domain.money import Money
from scripts import build_northfield_catalog as builder

HEADER = "Product_Code,Warehouse,Product_Category,Date,Order_Demand\n"
ROWS = [
    "Product_0007,Whse_J,Category_019,2012/7/27,1234567 ",
    "Product_0007,Whse_S,Category_019,2012/7/28,(4567)",
    "Product_0003,Whse_C,Category_006,2013/1/2,7654321 ",
    "Product_0009,Whse_A,Category_005,2014/3/4,12 ",
]


def _dataset(directory: Path, rows: list[str]) -> Path:
    """A zip shaped like Kaggle's download."""
    path = directory / "productdemand.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(builder.DATASET_FILE, HEADER + "".join(f"{row}\n" for row in rows))
    return path


def test_read_demand_totals_each_product_counting_parentheses_as_returns(tmp_path: Path) -> None:
    demand, categories = builder.read_demand(_dataset(tmp_path, ROWS))

    assert demand == {"Product_0007": 1230000, "Product_0003": 7654321, "Product_0009": 12}
    assert categories["Product_0007"] == "Category_019"


def test_read_demand_reads_the_extracted_csv_too(tmp_path: Path) -> None:
    path = tmp_path / builder.DATASET_FILE
    path.write_text(HEADER + ROWS[2] + "\n", encoding="utf-8")

    assert builder.read_demand(path) == (
        {"Product_0003": 7654321},
        {"Product_0003": "Category_006"},
    )


def test_read_demand_refuses_a_file_without_the_dataset_columns(tmp_path: Path) -> None:
    path = tmp_path / "other.csv"
    path.write_text("sku,quantity\nA-1,3\n", encoding="utf-8")

    with pytest.raises(builder.DatasetError, match="expected the columns"):
        builder.read_demand(path)


def test_top_products_ranks_by_total_demand_with_ties_to_the_lower_code() -> None:
    demand = {"Product_0003": 5, "Product_0002": 9, "Product_0001": 9, "Product_0004": 1}

    assert builder.top_products(demand, 3) == ["Product_0001", "Product_0002", "Product_0003"]


def test_build_catalog_keeps_the_top_products_by_code_and_none_of_their_demand(
    tmp_path: Path,
) -> None:
    items = builder.build_catalog(_dataset(tmp_path, ROWS), size=2)
    text = format_catalog(items)

    assert [item.dataset_code for item in items] == ["Product_0003", "Product_0007"]
    assert [item.sku for item in items] == ["SAF-0003", "FST-0007"]
    assert not any(figure in text for figure in ("1234567", "4567", "1230000", "7654321"))


def test_build_catalog_refuses_a_category_without_a_northfield_name(tmp_path: Path) -> None:
    path = _dataset(tmp_path, ["Product_0001,Whse_J,Category_002,2012/7/27,5"])

    with pytest.raises(builder.DatasetError, match="Category_002 has no Northfield category"):
        builder.build_catalog(path)


def test_catalog_for_rebuilds_the_committed_catalog_from_its_codes_alone() -> None:
    committed = northfield_catalog()

    rebuilt = builder.catalog_for({item.dataset_code: item.dataset_category for item in committed})

    assert rebuilt == list(committed)


@given(number=st.integers(0, 9999), category=st.sampled_from(sorted(builder.CATEGORIES)))
def test_describe_product_turns_any_code_into_the_same_valid_product(
    number: int, category: str
) -> None:
    code, spec = f"Product_{number:04d}", builder.CATEGORIES[category]

    item = builder.describe_product(code, category, spec)

    assert item == builder.describe_product(code, category, spec)
    assert (item.sku, item.category) == (f"{spec.sku_prefix}-{number:04d}", spec.name)
    assert 0 < item.unit_cost < item.list_price
    product = Product.create(
        tenant_id=uuid.uuid7(),
        currency="USD",
        sku=item.sku,
        name=item.name,
        unit=item.unit,
        list_price=Money(item.list_price, "USD"),
        unit_cost=Money(item.unit_cost, "USD"),
    )
    assert product.name == item.name


def test_describe_product_draws_again_when_the_name_is_taken() -> None:
    spec = builder.HAND_TOOLS
    first = builder.describe_product("Product_0001", "Category_032", spec)

    second = builder.describe_product("Product_0001", "Category_032", spec, frozenset({first.name}))

    assert second.name != first.name


def _name_hand_tools(count: int) -> None:
    taken = set[str]()
    for _ in range(count):
        item = builder.describe_product(
            "Product_0001", "Category_032", builder.HAND_TOOLS, frozenset(taken)
        )
        taken.add(item.name)


def test_describe_product_gives_up_when_no_name_is_left() -> None:
    with pytest.raises(builder.DatasetError, match="no free name left"):
        _name_hand_tools(100)  # hand tools have 14 possible names


def test_main_writes_the_catalog_file_and_says_what_it_wrote(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dataset, output = _dataset(tmp_path, ROWS), tmp_path / "data" / "catalog.csv"

    exit_code = builder.main([str(dataset), "--output", str(output), "--size", "2"])

    assert exit_code == 0
    assert list(parse_catalog(output.read_text(encoding="utf-8"))) == builder.build_catalog(
        dataset, size=2
    )
    assert "Wrote 2 products in 2 categories" in capsys.readouterr().out
