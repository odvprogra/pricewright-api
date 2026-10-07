"""Northfield Supply's catalog: the demand dataset's products, renamed (ADR-0025).

``data/northfield_catalog.csv`` maps each product code of the dataset that ``demand-forecast``
uses to a synthetic industrial or office supply: SKU, name, category, unit and prices. It holds no
demand figures, so loading the demo never needs the dataset. ``scripts/build_northfield_catalog.py``
writes it with ``format_catalog`` and this module reads it back.
"""

import csv
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from importlib import resources

from pricewright.domain.catalog import UnitOfMeasure

COLUMNS = (
    "dataset_code",
    "dataset_category",
    "sku",
    "name",
    "category",
    "unit",
    "list_price",
    "unit_cost",
)
DATASET_CODE = re.compile(r"Product_\d{4}")
DATASET_CATEGORY = re.compile(r"Category_\d{3}")
_FILE = "data/northfield_catalog.csv"


class CatalogFormatError(ValueError):
    """The catalog file does not have the expected columns or values."""


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """One product: the dataset's codes and what Northfield calls and charges for it (USD)."""

    dataset_code: str
    dataset_category: str
    sku: str
    name: str
    category: str
    unit: UnitOfMeasure
    list_price: Decimal
    unit_cost: Decimal


def _item(row: dict[str, str]) -> CatalogItem:
    if not DATASET_CODE.fullmatch(row["dataset_code"]):
        raise CatalogFormatError(f"not a dataset product code: {row['dataset_code']!r}")
    if not DATASET_CATEGORY.fullmatch(row["dataset_category"]):
        raise CatalogFormatError(f"not a dataset category: {row['dataset_category']!r}")
    return CatalogItem(
        dataset_code=row["dataset_code"],
        dataset_category=row["dataset_category"],
        sku=row["sku"],
        name=row["name"],
        category=row["category"],
        unit=UnitOfMeasure(row["unit"]),
        list_price=Decimal(row["list_price"]),
        unit_cost=Decimal(row["unit_cost"]),
    )


def parse_catalog(text: str) -> tuple[CatalogItem, ...]:
    """Read a catalog file. Other columns are refused, so a demand column cannot slip in."""
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise CatalogFormatError(f"expected the columns {', '.join(COLUMNS)}")
    return tuple(_item(row) for row in reader)


def format_catalog(items: Iterable[CatalogItem]) -> str:
    """The file's text, one row per item in the given order, with ``\\n`` line endings."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(COLUMNS)
    for item in items:
        writer.writerow(
            [
                item.dataset_code,
                item.dataset_category,
                item.sku,
                item.name,
                item.category,
                item.unit.value,
                str(item.list_price),
                str(item.unit_cost),
            ]
        )
    return output.getvalue()


def northfield_catalog() -> tuple[CatalogItem, ...]:
    """The committed catalog, sorted by dataset code."""
    text = resources.files("pricewright.demo").joinpath(_FILE).read_text(encoding="utf-8")
    return parse_catalog(text)
