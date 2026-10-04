"""The catalog: what a distributor sells (ADR-0016). Categories group products for pricing (M3)."""

import re
import uuid
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Literal

from pricewright.domain.errors import ConflictError, RuleViolationError
from pricewright.domain.money import Money

MAX_CATEGORY_NAME_LENGTH = 100
MAX_SKU_LENGTH = 40  # SAP S/4HANA's material numbers have up to 40 characters
MAX_PRODUCT_NAME_LENGTH = 200
_SKU = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


class InvalidCatalogError(RuleViolationError):
    code = "invalid_catalog"


class CategoryNameTakenError(ConflictError):
    code = "category_name_taken"


class SkuTakenError(ConflictError):
    code = "sku_taken"


class UnknownCategoryError(RuleViolationError):
    code = "unknown_category"


class UnitOfMeasure(StrEnum):
    """UN/ECE Recommendation 20 codes; packages are Recommendation 21 codes prefixed with X.

    Peppol and UBL documents use the same codes, so units on purchase orders map onto them.
    """

    EACH = "EA"
    PAIR = "PR"
    DOZEN = "DZN"
    SET = "SET"
    BAG = "XBG"
    BOX = "XBX"
    BUNDLE = "XBE"
    CARTON = "XCT"
    CASE = "XCS"
    PACKAGE = "XPK"
    PACKET = "XPA"
    PAIL = "XPL"
    ROLL = "XRO"
    GRAM = "GRM"
    KILOGRAM = "KGM"
    POUND = "LBR"
    METRE = "MTR"
    FOOT = "FOT"
    LITRE = "LTR"
    GALLON = "GLL"


class _Keep(Enum):
    KEEP = "keep"


KEEP = _Keep.KEEP
"""Leaves an optional field as it is, where ``None`` means "clear it"."""
type Keep = Literal[_Keep.KEEP]


def _category_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > MAX_CATEGORY_NAME_LENGTH:
        raise InvalidCatalogError(f"a category name has 1 to {MAX_CATEGORY_NAME_LENGTH} characters")
    return name


@dataclass(slots=True)
class ProductCategory:
    """A flat group of products (like SAP's material group). Names are unique, ignoring case."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    version: int = 1
    """Counts saved changes; the repository bumps it (ADR-0012)."""

    @classmethod
    def create(cls, *, tenant_id: uuid.UUID, name: str) -> ProductCategory:
        return cls(id=uuid.uuid7(), tenant_id=tenant_id, name=_category_name(name))

    def rename(self, name: str) -> None:
        self.name = _category_name(name)


def normalize_sku(sku: str) -> str:
    """Trimmed and checked; the case is kept, but two SKUs that differ only in case collide."""
    sku = sku.strip()
    if len(sku) > MAX_SKU_LENGTH or not _SKU.fullmatch(sku):
        raise InvalidCatalogError(
            f"a SKU has 1 to {MAX_SKU_LENGTH} letters, digits, '.', '_', '/' or '-', and starts "
            "with a letter or a digit"
        )
    return sku


def _product_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > MAX_PRODUCT_NAME_LENGTH:
        raise InvalidCatalogError(f"a product name has 1 to {MAX_PRODUCT_NAME_LENGTH} characters")
    return name


def _price(money: Money, *, currency: str, field: str) -> Money:
    if money.currency != currency:
        raise InvalidCatalogError(f"{field} must be in {currency}, the tenant's currency")
    if money.amount < 0:
        raise InvalidCatalogError(f"{field} cannot be negative")
    return money


@dataclass(slots=True)
class Product:
    """A sellable item. Never deleted once created: archived with ``is_active`` (ADR-0016).

    Prices are in the tenant's currency. A list price below the unit cost is allowed (a loss
    leader); the margin floor guard of the pricing engine flags it on quotes (M3).
    """

    id: uuid.UUID
    tenant_id: uuid.UUID
    sku: str
    name: str
    unit: UnitOfMeasure
    list_price: Money
    unit_cost: Money
    category_id: uuid.UUID | None = None
    is_active: bool = True
    version: int = 1
    """Counts saved changes; the repository bumps it (ADR-0012)."""

    @property
    def currency(self) -> str:
        return self.list_price.currency

    @classmethod
    def create(
        cls,
        *,
        tenant_id: uuid.UUID,
        currency: str,
        sku: str,
        name: str,
        unit: UnitOfMeasure,
        list_price: Money,
        unit_cost: Money,
        category_id: uuid.UUID | None = None,
    ) -> Product:
        return cls(
            id=uuid.uuid7(),
            tenant_id=tenant_id,
            sku=normalize_sku(sku),
            name=_product_name(name),
            unit=unit,
            list_price=_price(list_price, currency=currency, field="list_price"),
            unit_cost=_price(unit_cost, currency=currency, field="unit_cost"),
            category_id=category_id,
        )

    def change(
        self,
        *,
        name: str | None = None,
        unit: UnitOfMeasure | None = None,
        list_price: Money | None = None,
        unit_cost: Money | None = None,
        category_id: uuid.UUID | Literal[_Keep.KEEP] | None = KEEP,
        is_active: bool | None = None,
    ) -> None:
        """Fields left as ``None`` (``KEEP`` for the category) keep their value. The SKU is the
        product's identity for other systems and does not change."""
        currency = self.currency  # validate everything before changing anything
        new_name = self.name if name is None else _product_name(name)
        new_list_price = (
            self.list_price
            if list_price is None
            else _price(list_price, currency=currency, field="list_price")
        )
        new_unit_cost = (
            self.unit_cost
            if unit_cost is None
            else _price(unit_cost, currency=currency, field="unit_cost")
        )
        self.name, self.list_price, self.unit_cost = new_name, new_list_price, new_unit_cost
        self.unit = self.unit if unit is None else unit
        self.category_id = self.category_id if category_id is KEEP else category_id
        self.is_active = self.is_active if is_active is None else is_active
