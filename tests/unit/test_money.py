from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pricewright.domain.money import MAX_AMOUNT, InvalidMoneyError, Money

# Every amount NUMERIC(18, 4) can store.
storable_amounts = st.decimals(min_value=-MAX_AMOUNT, max_value=MAX_AMOUNT, places=4)


@given(amount=storable_amounts)
def test_money_keeps_every_storable_amount_exactly(amount: Decimal) -> None:
    money = Money(amount, "USD")

    assert money.amount == amount


@pytest.mark.parametrize("amount", ["0.00001", "12.34567", "NaN", "Infinity", "-Infinity"])
def test_money_rejects_amounts_the_database_cannot_store_exactly(amount: str) -> None:
    with pytest.raises(InvalidMoneyError, match="4 decimal places"):
        Money(Decimal(amount), "USD")


def test_money_rejects_a_decimal_built_from_a_float() -> None:
    with pytest.raises(InvalidMoneyError, match="4 decimal places"):
        Money(Decimal.from_float(0.1), "USD")


@pytest.mark.parametrize("amount", [MAX_AMOUNT + Decimal("0.0001"), -MAX_AMOUNT - 1])
def test_money_rejects_amounts_beyond_numeric_18_4(amount: Decimal) -> None:
    with pytest.raises(InvalidMoneyError, match="within"):
        Money(amount, "USD")


@pytest.mark.parametrize("currency", ["usd", "US", "USDX", "", "U$D", "ABC", "XAU", "CLF"])
def test_money_rejects_a_currency_that_is_not_an_iso_code(currency: str) -> None:
    with pytest.raises(InvalidMoneyError, match="ISO 4217"):
        Money(Decimal(1), currency)


def test_money_equality_ignores_trailing_zeros_but_not_the_currency() -> None:
    assert Money(Decimal("12.5"), "USD") == Money(Decimal("12.5000"), "USD")
    assert Money(Decimal("12.5"), "USD") != Money(Decimal("12.5"), "EUR")
