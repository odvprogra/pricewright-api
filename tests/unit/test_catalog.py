import uuid

import pytest

from pricewright.domain.catalog import (
    MAX_CATEGORY_NAME_LENGTH,
    InvalidCatalogError,
    ProductCategory,
)

TENANT_ID = uuid.uuid7()


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
