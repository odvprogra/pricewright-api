"""Build Northfield Supply's demo catalog from the demand dataset (ADR-0025).

Northfield sells the products of Kaggle's "Forecasts for Product Demand"
(``felixzhao/productdemandforecasting``, GPL-2) with the most demand, renamed as industrial and
office supplies, so the forecasts of ``demand-forecast`` map one to one onto Pricewright products.
The dataset is never committed: this script reads a downloaded copy and writes only codes and
synthetic names, units and prices, sorted by code, without any demand figure.

Public Kaggle datasets download without a token. To rebuild the catalog:

    curl -L -o productdemand.zip \\
        https://www.kaggle.com/api/v1/datasets/download/felixzhao/productdemandforecasting
    uv run python scripts/build_northfield_catalog.py productdemand.zip

Each product's name and prices come from a random generator seeded with its own code, using only
``random()``, the one method whose sequence Python keeps across versions: a product keeps its name
whichever other products are selected.
"""

import argparse
import csv
import io
import math
import random
import sys
import zipfile
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from string import Formatter
from typing import TextIO

from pricewright.demo.catalog import CatalogItem, format_catalog
from pricewright.domain.catalog import UnitOfMeasure

DATASET_FILE = "Historical Product Demand.csv"
DATASET_COLUMNS = frozenset({"Product_Code", "Product_Category", "Order_Demand"})
CATALOG_SIZE = 300
OUTPUT = Path(__file__).resolve().parents[1] / "src/pricewright/demo/data/northfield_catalog.csv"
SEED = "northfield-catalog"
MAX_NAME_ATTEMPTS = 50
CENT = Decimal("0.01")


class DatasetError(ValueError):
    """The dataset is not the expected one, or a product cannot be named."""


@dataclass(frozen=True, slots=True)
class Template:
    """A kind of product: a name pattern whose fields come from the category's slots."""

    pattern: str
    unit: UnitOfMeasure
    low: float
    """The cheapest list price, in USD."""
    high: float


@dataclass(frozen=True, slots=True)
class CategorySpec:
    name: str
    sku_prefix: str
    margin: tuple[float, float]
    """Gross margin on the list price: the cost is the list price times (1 - margin)."""
    templates: tuple[Template, ...]
    slots: Mapping[str, tuple[str, ...]]


_EA, _PR, _SET = UnitOfMeasure.EACH, UnitOfMeasure.PAIR, UnitOfMeasure.SET
_BAG, _BOX, _BUNDLE = UnitOfMeasure.BAG, UnitOfMeasure.BOX, UnitOfMeasure.BUNDLE
_CASE, _PACK, _ROLL = UnitOfMeasure.CASE, UnitOfMeasure.PACKAGE, UnitOfMeasure.ROLL

_SIZES = ("Small", "Medium", "Large", "X-Large", "2X-Large")
_SCENTS = ("Fresh Scent", "Lemon Scent", "Unscented")
_KELVIN = ("2700", "3000", "4000", "5000")

FASTENERS = CategorySpec(
    name="Fasteners & Fixings",
    sku_prefix="FST",
    margin=(0.34, 0.50),
    templates=(
        Template("{finish} Hex Cap Screw, {inch} x {inch_len}, Box of {pack}", _BOX, 9, 68),
        Template("{finish} Hex Cap Screw, {metric} x {metric_len}, Box of {pack}", _BOX, 9, 68),
        Template("{finish} Socket Head Cap Screw, {inch} x {inch_len}, Box of {few}", _BOX, 12, 75),
        Template(
            "{finish} Socket Head Cap Screw, {metric} x {metric_len}, Box of {few}", _BOX, 12, 75
        ),
        Template("{finish} Carriage Bolt, {inch} x {inch_len}, Box of {few}", _BOX, 10, 60),
        Template("{finish} Hex Nut, {inch}, Box of {pack}", _BOX, 5, 38),
        Template("{finish} Nylon-Insert Lock Nut, {metric}, Box of {pack}", _BOX, 7, 45),
        Template("{finish} Flat Washer, {washer}, Box of {pack}", _BOX, 4, 28),
        Template("{finish} Split Lock Washer, {washer}, Box of {pack}", _BOX, 4, 30),
        Template(
            "{finish} Flat Head Wood Screw, {gauge} x {screw_len}, Box of {pack}", _BOX, 6, 34
        ),
        Template(
            "{finish} Hex Washer Head Self-Drilling Screw, {gauge} x {screw_len}, Box of {pack}",
            _BOX,
            8,
            42,
        ),
        Template(
            "{finish} Pan Head Machine Screw, {gauge} x {screw_len}, Box of {pack}", _BOX, 6, 30
        ),
        Template("{finish} Hex Lag Screw, {anchor} x {screw_len}, Box of {few}", _BOX, 9, 55),
        Template("Zinc-Plated Wedge Anchor, {anchor} x {anchor_len}, Box of {few}", _BOX, 14, 85),
        Template("Zinc-Plated Sleeve Anchor, {anchor} x {anchor_len}, Box of {few}", _BOX, 12, 70),
        Template("{rivet} Blind Rivet, {rivet_dia} x {grip} Grip, Box of {pack}", _BOX, 7, 40),
        Template("{finish} Threaded Rod, {inch} x {rod_len}", _EA, 4, 36),
        Template("Ribbed Plastic Wall Anchor with Screws, {gauge}, Pack of {pack}", _PACK, 6, 24),
    ),
    slots={
        "finish": (
            "Zinc-Plated Steel",
            "18-8 Stainless Steel",
            "Black-Oxide Steel",
            "Hot-Dip Galvanized Steel",
            "Grade 5 Zinc-Plated Steel",
            "Grade 8 Yellow-Zinc Steel",
        ),
        "inch": ("1/4-20", "5/16-18", "3/8-16", "1/2-13", "5/8-11"),
        "inch_len": ("1/2 in", "3/4 in", "1 in", "1-1/4 in", "1-1/2 in", "2 in", "3 in", "4 in"),
        "metric": ("M5 x 0.8", "M6 x 1.0", "M8 x 1.25", "M10 x 1.5", "M12 x 1.75"),
        "metric_len": ("12 mm", "16 mm", "20 mm", "25 mm", "30 mm", "40 mm", "50 mm", "60 mm"),
        "gauge": ("#6", "#8", "#10", "#12", "#14"),
        "screw_len": ("1/2 in", "3/4 in", "1 in", "1-1/4 in", "1-1/2 in", "2 in", "3 in"),
        "washer": ("#10", "1/4 in", "5/16 in", "3/8 in", "1/2 in", "M6", "M8", "M10", "M12"),
        "anchor": ("1/4 in", "3/8 in", "1/2 in", "5/8 in"),
        "anchor_len": ("2-1/4 in", "3 in", "3-3/4 in", "4-1/4 in", "5-1/2 in", "7 in"),
        "rivet": ("Aluminum", "Steel", "Stainless Steel"),
        "rivet_dia": ("1/8 in", "5/32 in", "3/16 in"),
        "grip": ("0.126-0.187 in", "0.188-0.250 in", "0.251-0.375 in"),
        "rod_len": ("3 ft", "6 ft", "10 ft"),
        "pack": ("50", "100", "250"),
        "few": ("25", "50"),
    },
)

SAFETY = CategorySpec(
    name="Safety & PPE",
    sku_prefix="SAF",
    margin=(0.30, 0.48),
    templates=(
        Template(
            "Nitrile Disposable Gloves, {mil} mil, Powder-Free, {size}, Box of 100", _BOX, 9, 22
        ),
        Template("Cut-Resistant Gloves, ANSI {cut}, {palm} Palm, {size}", _PR, 5, 19),
        Template("Cowhide Leather Work Gloves, Keystone Thumb, {size}", _PR, 6, 17),
        Template("Safety Glasses, {lens} Lens, Anti-Fog, ANSI Z87.1+", _EA, 3, 14),
        Template("Indirect-Vent Safety Goggles, {lens} Lens", _EA, 6, 18),
        Template("Hard Hat, Type I Class E, {hat}, 4-Point Ratchet Suspension", _EA, 12, 38),
        Template("High-Visibility Safety Vest, ANSI Class 2, {vis}, {size}", _EA, 7, 24),
        Template("Foam Earplugs, NRR {plug_nrr} dB, Uncorded, Box of 200 Pairs", _BOX, 18, 42),
        Template("Over-the-Head Earmuffs, NRR {muff_nrr} dB", _EA, 14, 38),
        Template("N95 Particulate Respirator, {valve}, Box of {masks}", _BOX, 14, 44),
        Template("First Aid Kit, ANSI Class {kit_class}, {people}-Person", _EA, 24, 120),
        Template("Hooded Disposable Coveralls, {size}, Case of 25", _CASE, 58, 140),
    ),
    slots={
        "mil": ("4", "5", "6", "8"),
        "size": _SIZES,
        "cut": ("A2", "A3", "A4", "A5", "A6"),
        "palm": ("Polyurethane-Coated", "Nitrile-Coated", "Latex-Coated"),
        "lens": ("Clear", "Gray", "Amber", "Indoor/Outdoor Mirror", "Blue Mirror"),
        "hat": ("White", "Yellow", "Blue", "Orange", "Green", "Red"),
        "vis": ("Lime", "Orange"),
        "plug_nrr": ("29", "32", "33"),
        "muff_nrr": ("22", "25", "27", "30"),
        "valve": ("With Exhalation Valve", "Without Valve"),
        "masks": ("10", "20"),
        "kit_class": ("A", "B"),
        "people": ("10", "25", "50", "100"),
    },
)

OFFICE_PAPER = CategorySpec(
    name="Office Paper & Mailing",
    sku_prefix="OFP",
    margin=(0.18, 0.32),
    templates=(
        Template("Copy Paper, {paper}, {weight}, {bright} Bright, Case of 10 Reams", _CASE, 42, 98),
        Template("Kraft Clasp Envelopes, {envelope}, Box of {envelopes}", _BOX, 14, 62),
        Template("Poly Bubble Mailers, {mailer}, Pack of {mailers}", _PACK, 9, 48),
        Template("Shipping Labels, {label}, White, Box of {labels}", _BOX, 12, 64),
        Template("Sticky Notes, {note}, {note_color}, Pack of 12 Pads", _PACK, 6, 19),
        Template("File Folders, Letter, {tab} Tab, {folder}, Box of 100", _BOX, 11, 34),
        Template("Cardstock, White, {card}, Letter, Pack of 250 Sheets", _PACK, 13, 36),
        Template("Thermal Receipt Paper Rolls, 3-1/8 in x 230 ft, Case of {rolls}", _CASE, 28, 69),
        Template("Ruled Writing Pads, {pad}, {pad_color}, Pack of 12", _PACK, 9, 24),
    ),
    slots={
        "paper": ("Letter 8.5 x 11 in", "Legal 8.5 x 14 in", "Ledger 11 x 17 in"),
        "weight": ("20 lb", "24 lb", "28 lb"),
        "bright": ("92", "96", "98"),
        "envelope": ("6 x 9 in", "9 x 12 in", "10 x 13 in", "10 x 15 in"),
        "envelopes": ("100", "250", "500"),
        "mailer": ("#0 6 x 10 in", "#2 8.5 x 12 in", "#4 9.5 x 14.5 in", "#6 12.5 x 19 in"),
        "mailers": ("25", "50", "100"),
        "label": ("1 x 2-5/8 in", "2 x 4 in", "3-1/3 x 4 in", "4 x 6 in"),
        "labels": ("500", "1,000", "3,000"),
        "note": ("3 x 3 in", "3 x 5 in", "4 x 6 in"),
        "note_color": ("Yellow", "Assorted Pastel", "Assorted Bright"),
        "tab": ("1/3-Cut", "1/5-Cut", "Straight-Cut"),
        "folder": ("Manila", "Assorted Colors", "Kraft"),
        "card": ("65 lb", "80 lb", "110 lb"),
        "rolls": ("24", "50"),
        "pad": ("Letter 8.5 x 11.75 in", "Junior 5 x 8 in"),
        "pad_color": ("White", "Canary"),
    },
)

JANITORIAL = CategorySpec(
    name="Janitorial & Sanitation",
    sku_prefix="JAN",
    margin=(0.28, 0.42),
    templates=(
        Template(
            "Trash Can Liners, {liner}, {liner_mil} mil, Black, Case of {liners}", _CASE, 24, 68
        ),
        Template("Hardwound Paper Towel Rolls, {towel}, 800 ft, Case of 6", _CASE, 32, 64),
        Template("Multi-Surface Cleaner Concentrate, {scent}, 1 gal, Case of 4", _CASE, 34, 76),
        Template("Foaming Hand Soap Refill, {soap}, 1,000 mL, Case of 6", _CASE, 38, 84),
        Template("Microfiber Cleaning Cloths, {cloth}, {cloth_color}, Pack of 24", _PACK, 14, 38),
        Template("Bath Tissue, 2-Ply, {sheets} Sheets per Roll, Case of 96 Rolls", _CASE, 48, 96),
        Template("Disinfecting Wipes, Canister of 80, {scent}, Case of 6", _CASE, 24, 52),
    ),
    slots={
        "liner": ("33 gal", "45 gal", "55-60 gal"),
        "liner_mil": ("1.2", "1.5", "2.0"),
        "liners": ("100", "150"),
        "towel": ("White", "Natural"),
        "scent": _SCENTS,
        "soap": ("Unscented", "Antibacterial", "Mild Fragrance"),
        "cloth": ("12 x 12 in", "16 x 16 in"),
        "cloth_color": ("Blue", "Assorted Colors"),
        "sheets": ("400", "500"),
    },
)

PACKAGING = CategorySpec(
    name="Packaging & Shipping",
    sku_prefix="PKG",
    margin=(0.26, 0.40),
    templates=(
        Template("Carton Sealing Tape, {tape}, 2 in x 110 yd, Case of 36", _CASE, 48, 98),
        Template("Stretch Wrap Film, {wrap} x 1,500 ft, {gauge} Gauge, Case of 4", _CASE, 54, 118),
        Template("Corrugated Shipping Boxes, {box}, {strength}, Bundle of 25", _BUNDLE, 22, 74),
        Template("Air Bubble Cushioning Roll, {bubble}, 12 in x 175 ft", _ROLL, 18, 46),
        Template("Kraft Packing Paper Roll, 30 lb, 24 in x {kraft}", _ROLL, 26, 72),
        Template("Loose-Fill Packing Peanuts, {fill}, 14 cu ft Bag", _BAG, 24, 52),
    ),
    slots={
        "tape": ("Clear", "Tan"),
        "wrap": ("15 in", "18 in", "20 in"),
        "gauge": ("70", "80", "90"),
        "box": ("8 x 8 x 8 in", "12 x 12 x 12 in", "16 x 12 x 8 in", "18 x 18 x 16 in"),
        "strength": ("32 ECT", "44 ECT"),
        "bubble": ("3/16 in Bubbles", "5/16 in Bubbles", "1/2 in Bubbles"),
        "kraft": ("600 ft", "900 ft", "1,200 ft"),
        "fill": ("White", "Green"),
    },
)

ELECTRICAL = CategorySpec(
    name="Electrical & Lighting",
    sku_prefix="ELC",
    margin=(0.30, 0.45),
    templates=(
        Template("LED A19 Light Bulb, 800 lm, {kelvin} K, Dimmable, Pack of 4", _PACK, 9, 24),
        Template("LED T8 Tube Lamp, 4 ft, {kelvin} K, Case of 10", _CASE, 58, 128),
        Template("Extension Cord, 12/3 AWG, {cord}, {cord_color}", _EA, 28, 96),
        Template("Nylon Cable Ties, {tie}, {tensile} Tensile, Bag of 100", _BAG, 5, 19),
        Template("Vinyl Electrical Tape, 3/4 in x 66 ft, {tape_color}, Pack of 10", _PACK, 9, 26),
    ),
    slots={
        "kelvin": _KELVIN,
        "cord": ("25 ft", "50 ft", "100 ft"),
        "cord_color": ("Yellow", "Orange", "Blue"),
        "tie": ("8 in", "11 in", "14 in"),
        "tensile": ("40 lb", "50 lb", "75 lb"),
        "tape_color": ("Black", "Red", "Assorted Colors"),
    },
)

ADHESIVES = CategorySpec(
    name="Adhesives & Sealants",
    sku_prefix="ADH",
    margin=(0.32, 0.46),
    templates=(
        Template("Silicone Sealant, {sealant}, 10.1 oz Cartridge, Case of 12", _CASE, 48, 96),
        Template("Construction Adhesive, {grade}, 10 oz Cartridge, Case of 12", _CASE, 42, 88),
        Template("Threadlocker, {strength} Strength, 50 mL Bottle", _EA, 9, 28),
        Template("Two-Part Epoxy, {cure}, 1 oz Syringe, Pack of 6", _PACK, 18, 44),
    ),
    slots={
        "sealant": ("Clear", "White", "Black"),
        "grade": ("General Purpose", "Heavy Duty", "Subfloor"),
        "strength": ("Low", "Medium", "High"),
        "cure": ("5-Minute Cure", "30-Minute Cure"),
    },
)

HAND_TOOLS = CategorySpec(
    name="Hand Tools",
    sku_prefix="TLS",
    margin=(0.34, 0.48),
    templates=(
        Template("Combination Wrench Set, {system}, {wrenches}-Piece", _SET, 34, 120),
        Template("Cushion-Grip Screwdriver Set, {drivers}-Piece", _SET, 18, 56),
        Template("Tape Measure, {tape_len}, Magnetic Hook", _EA, 12, 34),
        Template("Retractable Utility Knife with {blades} Blades", _EA, 8, 22),
    ),
    slots={
        "system": ("SAE", "Metric"),
        "wrenches": ("7", "11", "14"),
        "drivers": ("6", "8", "10"),
        "tape_len": ("16 ft", "25 ft", "30 ft"),
        "blades": ("5", "10"),
    },
)

CATEGORIES: Mapping[str, CategorySpec] = {
    "Category_019": FASTENERS,
    "Category_006": SAFETY,
    "Category_005": OFFICE_PAPER,
    "Category_028": JANITORIAL,
    "Category_030": PACKAGING,
    "Category_007": ELECTRICAL,
    "Category_033": ADHESIVES,
    "Category_032": HAND_TOOLS,
}
"""The dataset's categories among its top products, and what Northfield calls them."""


def _quantity(text: str) -> int:
    """Units of an order line; the dataset writes returns in parentheses: ``(100)``."""
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        return -int(text[1:-1])
    return int(text)


@contextmanager
def _open(path: Path) -> Iterator[TextIO]:
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive, archive.open(DATASET_FILE) as raw:
            yield io.TextIOWrapper(raw, encoding="utf-8", newline="")
    else:
        with path.open(encoding="utf-8", newline="") as text:
            yield text


def read_demand(path: Path) -> tuple[Counter[str], dict[str, str]]:
    """Total demand and category per product code, from the downloaded zip or its CSV."""
    demand: Counter[str] = Counter()
    categories: dict[str, str] = {}
    with _open(path) as text:
        reader = csv.DictReader(text)
        if not set(reader.fieldnames or ()) >= DATASET_COLUMNS:
            raise DatasetError(f"expected the columns {', '.join(sorted(DATASET_COLUMNS))}")
        for row in reader:
            code = row["Product_Code"]
            demand[code] += _quantity(row["Order_Demand"])
            categories[code] = row["Product_Category"]
    return demand, categories


def top_products(demand: Mapping[str, int], size: int) -> list[str]:
    """The ``size`` codes with the most demand; ties go to the lower code."""
    return sorted(demand, key=lambda code: (-demand[code], code))[:size]


def _pick[T](rng: random.Random, options: Sequence[T]) -> T:
    return options[int(rng.random() * len(options))]


def _fill(pattern: str, slots: Mapping[str, Sequence[str]], rng: random.Random) -> str:
    chosen: dict[str, str] = {}
    for _, field, _, _ in Formatter().parse(pattern):
        if field is not None and field not in chosen:
            chosen[field] = _pick(rng, slots[field])
    return pattern.format_map(chosen)


def _cents(value: float) -> Decimal:
    return Decimal(repr(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def describe_product(
    code: str, dataset_category: str, spec: CategorySpec, taken: frozenset[str] = frozenset()
) -> CatalogItem:
    """Name and price the product with a generator seeded by its code; a name in ``taken`` is
    drawn again, so names stay unique within the catalog."""
    rng = random.Random(f"{SEED}/{code}")
    for _ in range(MAX_NAME_ATTEMPTS):
        template = _pick(rng, spec.templates)
        name = _fill(template.pattern, spec.slots, rng)
        if name not in taken:
            break
    else:
        raise DatasetError(f"no free name left for {code} in {spec.name}")
    spread = math.log(template.high) - math.log(template.low)
    list_price = _cents(math.exp(math.log(template.low) + rng.random() * spread))
    low, high = spec.margin
    margin = Decimal(repr(low + rng.random() * (high - low)))
    return CatalogItem(
        dataset_code=code,
        dataset_category=dataset_category,
        sku=f"{spec.sku_prefix}-{code.removeprefix('Product_')}",
        name=name,
        category=spec.name,
        unit=template.unit,
        list_price=list_price,
        unit_cost=(list_price * (1 - margin)).quantize(CENT, rounding=ROUND_HALF_UP),
    )


def catalog_for(products: Mapping[str, str]) -> list[CatalogItem]:
    """The catalog of these product codes (code → dataset category), sorted by code."""
    items: list[CatalogItem] = []
    taken: set[str] = set()
    for code in sorted(products):
        spec = CATEGORIES.get(products[code])
        if spec is None:
            raise DatasetError(f"{products[code]} has no Northfield category; add it to CATEGORIES")
        item = describe_product(code, products[code], spec, frozenset(taken))
        taken.add(item.name)
        items.append(item)
    return items


def build_catalog(path: Path, *, size: int = CATALOG_SIZE) -> list[CatalogItem]:
    """The catalog of the ``size`` products with the most demand in the dataset at ``path``."""
    demand, categories = read_demand(path)
    return catalog_for({code: categories[code] for code in top_products(demand, size)})


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Northfield's catalog (ADR-0025).")
    parser.add_argument("dataset", type=Path, help="the downloaded zip, or its CSV")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--size", type=int, default=CATALOG_SIZE)
    args = parser.parse_args(argv)
    items = build_catalog(args.dataset, size=args.size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(format_catalog(items), encoding="utf-8", newline="\n")
    categories = len({item.category for item in items})
    sys.stdout.write(f"Wrote {len(items)} products in {categories} categories to {args.output}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
