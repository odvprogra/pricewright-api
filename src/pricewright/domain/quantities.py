"""Quantities on quote lines and in volume brackets.

Positive, with up to three decimals, as SAP's quantity fields carry, for units such as kilograms and
metres. At most ten digits: a unit price (18 digits, ADR-0003) times a quantity then has at most 28
digits, which the default decimal context multiplies exactly.
"""

from decimal import Decimal

from pricewright.domain.errors import RuleViolationError

QUANTITY_DECIMAL_PLACES = 3
MAX_QUANTITY = Decimal("9999999.999")


class InvalidQuantityError(RuleViolationError):
    code = "invalid_quantity"


def quantity(value: Decimal) -> Decimal:
    """The value, if it is a valid quantity."""
    exponent = value.as_tuple().exponent
    if (
        not isinstance(exponent, int)
        or -exponent > QUANTITY_DECIMAL_PLACES
        or not 0 < value <= MAX_QUANTITY
    ):
        raise InvalidQuantityError(
            f"a quantity is greater than 0 and at most {MAX_QUANTITY}, with up to "
            f"{QUANTITY_DECIMAL_PLACES} decimal places"
        )
    return value
