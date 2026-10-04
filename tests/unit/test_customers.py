import uuid

import pytest

from pricewright.domain.customers import (
    DEFAULT_PAYMENT_TERMS_DAYS,
    Customer,
    CustomerTier,
    InvalidCustomerError,
    compact_tax_id,
)
from pricewright.domain.updates import KEEP

TENANT_ID = uuid.uuid7()


def acme(**changes: str | int | None) -> Customer:
    tax_id = changes.get("tax_id", "de 123.456-789")
    return Customer.create(
        tenant_id=TENANT_ID,
        account_number=str(changes.get("account_number", " C-1001 ")),
        name=str(changes.get("name", " Acme Industrial ")),
        tier=CustomerTier.GOLD,
        payment_terms_days=int(changes.get("payment_terms_days") or 60),
        tax_id=None if tax_id is None else str(tax_id),
    )


def test_customer_create_trims_and_compacts_its_fields() -> None:
    customer = acme()

    assert (customer.account_number, customer.name, customer.tax_id) == (
        "C-1001",
        "Acme Industrial",
        "DE123456789",
    )
    assert (customer.tier, customer.payment_terms_days, customer.is_active) == (
        CustomerTier.GOLD,
        60,
        True,
    )
    assert customer.id.version == 7


def test_customer_defaults_to_standard_tier_and_net_30() -> None:
    customer = Customer.create(tenant_id=TENANT_ID, account_number="C-1", name="Acme")

    assert (customer.tier, customer.payment_terms_days, customer.tax_id) == (
        CustomerTier.STANDARD,
        DEFAULT_PAYMENT_TERMS_DAYS,
        None,
    )


@pytest.mark.parametrize("raw", ["de 123.456-789", "DE123456789", "De-123/456 789"])
def test_tax_ids_written_differently_compact_to_the_same_value(raw: str) -> None:
    assert compact_tax_id(raw) == "DE123456789"


@pytest.mark.parametrize("raw", ["", " - ", "ÄÖ123", "x" * 31, "12#34"])
def test_a_tax_id_has_1_to_30_letters_and_digits(raw: str) -> None:
    with pytest.raises(InvalidCustomerError, match="tax id"):
        compact_tax_id(raw)


@pytest.mark.parametrize("account_number", ["", "-1", "has space", "x" * 21])
def test_an_account_number_is_a_code_of_1_to_20_characters(account_number: str) -> None:
    with pytest.raises(InvalidCustomerError, match="account number"):
        acme(account_number=account_number)


@pytest.mark.parametrize("days", [-1, 366])
def test_payment_terms_are_0_to_365_net_days(days: int) -> None:
    with pytest.raises(InvalidCustomerError, match="payment terms"):
        Customer.create(
            tenant_id=TENANT_ID, account_number="C-1", name="Acme", payment_terms_days=days
        )


def test_due_on_receipt_is_zero_days() -> None:
    customer = Customer.create(
        tenant_id=TENANT_ID, account_number="C-1", name="Acme", payment_terms_days=0
    )

    assert customer.payment_terms_days == 0


@pytest.mark.parametrize("name", ["", "  ", "x" * 201])
def test_a_customer_name_has_1_to_200_characters(name: str) -> None:
    with pytest.raises(InvalidCustomerError, match="customer name"):
        acme(name=name)


def test_customer_change_keeps_what_is_not_sent() -> None:
    customer = acme()

    customer.change(tier=CustomerTier.SILVER, is_active=False)

    assert (customer.tier, customer.is_active, customer.tax_id, customer.name) == (
        CustomerTier.SILVER,
        False,
        "DE123456789",
        "Acme Industrial",
    )


def test_customer_change_sets_compacts_or_clears_the_tax_id() -> None:
    customer = acme()

    customer.change(tax_id="fr 12 345")
    compacted = customer.tax_id
    customer.change(name="Acme", tax_id=KEEP)
    customer.change(tax_id=None)

    assert (compacted, customer.tax_id, customer.name) == ("FR12345", None, "Acme")


def test_customer_change_is_all_or_nothing() -> None:
    customer = acme()

    with pytest.raises(InvalidCustomerError):
        customer.change(name="Renamed", payment_terms_days=400)

    assert (customer.name, customer.payment_terms_days) == ("Acme Industrial", 60)
