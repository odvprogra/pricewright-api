from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pricewright.domain.quantities import MAX_QUANTITY, InvalidQuantityError, quantity


@given(value=st.decimals(min_value=Decimal("0.001"), max_value=MAX_QUANTITY, places=3))
def test_quantity_accepts_every_positive_value_with_up_to_3_places(value: Decimal) -> None:
    assert quantity(value) == value


@pytest.mark.parametrize("value", ["0", "-1", "0.0001", "10000000", "NaN", "Infinity"])
def test_quantity_rejects_zero_negative_too_precise_or_too_large_values(value: str) -> None:
    with pytest.raises(InvalidQuantityError, match="quantity"):
        quantity(Decimal(value))
