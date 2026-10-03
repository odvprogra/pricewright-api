from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pricewright.domain.tenants import (
    DEFAULT_APPROVAL_THRESHOLD,
    InvalidTenantError,
    Tenant,
    TenantSettings,
)

# Rates with at most four decimal places, as NUMERIC(5, 4) stores them.
rates_below_one = st.decimals(min_value=0, max_value=Decimal("0.9999"), places=4)
rates_up_to_one = st.decimals(min_value=0, max_value=1, places=4)


@given(tax_rate=rates_below_one, threshold=rates_up_to_one)
def test_tenant_settings_accepts_every_rate_in_range(tax_rate: Decimal, threshold: Decimal) -> None:
    settings = TenantSettings(currency="USD", tax_rate=tax_rate, approval_threshold=threshold)

    assert (settings.tax_rate, settings.approval_threshold) == (tax_rate, threshold)


def test_tenant_settings_default_approval_threshold_is_fifteen_percent() -> None:
    settings = TenantSettings(currency="USD", tax_rate=Decimal("0.07"))

    assert settings.approval_threshold == DEFAULT_APPROVAL_THRESHOLD == Decimal("0.15")


@pytest.mark.parametrize("currency", ["usd", "US", "USDX", "", "U$D"])
def test_tenant_settings_rejects_currency_that_is_not_an_iso_code(currency: str) -> None:
    with pytest.raises(InvalidTenantError, match="currency"):
        TenantSettings(currency=currency, tax_rate=Decimal(0))


@pytest.mark.parametrize("tax_rate", ["-0.01", "1", "1.5", "0.07251", "NaN", "Infinity"])
def test_tenant_settings_rejects_invalid_tax_rate(tax_rate: str) -> None:
    with pytest.raises(InvalidTenantError, match="tax_rate"):
        TenantSettings(currency="USD", tax_rate=Decimal(tax_rate))


@pytest.mark.parametrize("threshold", ["-0.0001", "1.0001", "0.15001"])
def test_tenant_settings_rejects_invalid_approval_threshold(threshold: str) -> None:
    with pytest.raises(InvalidTenantError, match="approval_threshold"):
        TenantSettings(currency="USD", tax_rate=Decimal(0), approval_threshold=Decimal(threshold))


def test_tenant_register_trims_the_name_and_assigns_a_uuid7() -> None:
    tenant = Tenant.register(
        name="  Northfield Supply  ", settings=TenantSettings(currency="USD", tax_rate=Decimal(0))
    )

    assert tenant.name == "Northfield Supply"
    assert tenant.id.version == 7


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_tenant_register_rejects_blank_or_too_long_name(name: str) -> None:
    with pytest.raises(InvalidTenantError, match="name"):
        Tenant.register(name=name, settings=TenantSettings(currency="USD", tax_rate=Decimal(0)))
