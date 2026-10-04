"""Money: an exact amount in one currency, never a float (ADR-0003).

Amounts carry at most four decimal places, what ``NUMERIC(18, 4)`` stores: B2B unit prices go below
the cent. Nothing here rounds; rounding happens only at the points ADR-0003 defines (from M3).
"""

from dataclasses import dataclass
from decimal import Decimal

from pricewright.domain.currencies import is_iso_4217
from pricewright.domain.errors import RuleViolationError

AMOUNT_DECIMAL_PLACES = 4
MAX_AMOUNT = Decimal("99999999999999.9999")
"""The largest magnitude ``NUMERIC(18, 4)`` holds: 14 integer digits and 4 decimal places."""


class InvalidMoneyError(RuleViolationError):
    code = "invalid_money"


@dataclass(frozen=True, slots=True)
class Money:
    """An amount and its ISO 4217 currency, always together (Fowler's Money pattern).

    Equality is by value: 12.5 and 12.5000 USD are the same money.
    """

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not is_iso_4217(self.currency):
            raise InvalidMoneyError("currency must be an ISO 4217 code such as USD")
        # Also catches most floats in disguise: Decimal(0.1) has 55 decimal places.
        exponent = self.amount.as_tuple().exponent
        if not isinstance(exponent, int) or -exponent > AMOUNT_DECIMAL_PLACES:
            raise InvalidMoneyError(
                f"amounts are numbers with at most {AMOUNT_DECIMAL_PLACES} decimal places"
            )
        if abs(self.amount) > MAX_AMOUNT:
            raise InvalidMoneyError(f"amounts must be within ±{MAX_AMOUNT}")
