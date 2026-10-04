import re

import pytest

from pricewright.domain.currencies import MINOR_UNITS, is_iso_4217


@pytest.mark.parametrize(
    ("code", "minor_units"),
    [("JPY", 0), ("CLP", 0), ("USD", 2), ("EUR", 2), ("BOB", 2), ("KWD", 3), ("IQD", 3)],
)
def test_currencies_minor_units_follow_iso_4217(code: str, minor_units: int) -> None:
    assert MINOR_UNITS[code] == minor_units


def test_currencies_cover_every_current_iso_4217_currency_with_minor_units() -> None:
    assert len(MINOR_UNITS) == 155
    assert all(re.fullmatch(r"[A-Z]{3}", code) for code in MINOR_UNITS)
    # Four places for unit prices are always finer than a document amount's minor units.
    assert max(MINOR_UNITS.values()) == 3


@pytest.mark.parametrize("code", ["CLF", "UYW", "BOV", "XAU", "XDR", "XTS", "XXX", "ABC", "usd"])
def test_currencies_exclude_funds_metals_test_codes_and_unknown_codes(code: str) -> None:
    assert not is_iso_4217(code)
