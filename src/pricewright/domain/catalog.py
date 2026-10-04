"""The catalog: what a distributor sells. Categories group products for pricing rules (M3)."""

import uuid
from dataclasses import dataclass

from pricewright.domain.errors import ConflictError, RuleViolationError

MAX_CATEGORY_NAME_LENGTH = 100


class InvalidCatalogError(RuleViolationError):
    code = "invalid_catalog"


class CategoryNameTakenError(ConflictError):
    code = "category_name_taken"


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
