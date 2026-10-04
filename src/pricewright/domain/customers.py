"""Customers: the businesses a distributor quotes to (ADR-0016).

A customer is keyed by its account number, never deleted (archived with ``is_active``), and holds
what pricing and quotes read: its tier (customer-tier discounts, M3) and its payment terms.
"""

import re
import uuid
from dataclasses import dataclass
from enum import StrEnum

from pricewright.domain.errors import ConflictError, RuleViolationError
from pricewright.domain.updates import KEEP, Keep

MAX_ACCOUNT_NUMBER_LENGTH = 20  # Dynamics 365's customer account has 20 characters
MAX_CUSTOMER_NAME_LENGTH = 200
MAX_TAX_ID_LENGTH = 30
MAX_PAYMENT_TERMS_DAYS = 365
DEFAULT_PAYMENT_TERMS_DAYS = 30  # "net 30", the usual B2B terms
_ACCOUNT_NUMBER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_TAX_ID_SEPARATORS = re.compile(r"[\s./-]")
_COMPACT_TAX_ID = re.compile(r"[A-Z0-9]+")


class InvalidCustomerError(RuleViolationError):
    code = "invalid_customer"


class AccountNumberTakenError(ConflictError):
    code = "account_number_taken"


class UnknownCustomerError(RuleViolationError):
    code = "unknown_customer"


class CustomerTier(StrEnum):
    """Read by customer-tier discounts (M3)."""

    STANDARD = "standard"
    SILVER = "silver"
    GOLD = "gold"


def normalize_account_number(account_number: str) -> str:
    """Trimmed and checked; the case is kept, but numbers that differ only in case collide."""
    account_number = account_number.strip()
    if len(account_number) > MAX_ACCOUNT_NUMBER_LENGTH or not _ACCOUNT_NUMBER.fullmatch(
        account_number
    ):
        raise InvalidCustomerError(
            f"an account number has 1 to {MAX_ACCOUNT_NUMBER_LENGTH} letters, digits, '.', '_', "
            "'/' or '-', and starts with a letter or a digit"
        )
    return account_number


def compact_tax_id(tax_id: str) -> str:
    """The same id written differently compares equal: ``de 123.456-789`` is ``DE123456789``."""
    compact = _TAX_ID_SEPARATORS.sub("", tax_id).upper()
    if len(compact) > MAX_TAX_ID_LENGTH or not _COMPACT_TAX_ID.fullmatch(compact):
        raise InvalidCustomerError(
            f"a tax id has 1 to {MAX_TAX_ID_LENGTH} letters and digits, besides spaces, dots, "
            "slashes and hyphens"
        )
    return compact


def _name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > MAX_CUSTOMER_NAME_LENGTH:
        raise InvalidCustomerError(
            f"a customer name has 1 to {MAX_CUSTOMER_NAME_LENGTH} characters"
        )
    return name


def _payment_terms(days: int) -> int:
    if not 0 <= days <= MAX_PAYMENT_TERMS_DAYS:
        raise InvalidCustomerError(
            f"payment terms are 0 (due on receipt) to {MAX_PAYMENT_TERMS_DAYS} net days"
        )
    return days


@dataclass(slots=True)
class Customer:
    id: uuid.UUID
    tenant_id: uuid.UUID
    account_number: str
    name: str
    tier: CustomerTier = CustomerTier.STANDARD
    payment_terms_days: int = DEFAULT_PAYMENT_TERMS_DAYS
    """Net days to pay; 0 is due on receipt."""
    tax_id: str | None = None
    """Compact; not unique: branches of one legal entity share it."""
    is_active: bool = True
    version: int = 1
    """Counts saved changes; the repository bumps it (ADR-0012)."""

    @classmethod
    def create(
        cls,
        *,
        tenant_id: uuid.UUID,
        account_number: str,
        name: str,
        tier: CustomerTier = CustomerTier.STANDARD,
        payment_terms_days: int = DEFAULT_PAYMENT_TERMS_DAYS,
        tax_id: str | None = None,
    ) -> Customer:
        return cls(
            id=uuid.uuid7(),
            tenant_id=tenant_id,
            account_number=normalize_account_number(account_number),
            name=_name(name),
            tier=tier,
            payment_terms_days=_payment_terms(payment_terms_days),
            tax_id=None if tax_id is None else compact_tax_id(tax_id),
        )

    def change(
        self,
        *,
        name: str | None = None,
        tier: CustomerTier | None = None,
        payment_terms_days: int | None = None,
        tax_id: str | Keep | None = KEEP,
        is_active: bool | None = None,
    ) -> None:
        """Fields left as ``None`` (``KEEP`` for the tax id) keep their value. The account number
        is the customer's identity for other systems and does not change."""
        new_name = self.name if name is None else _name(name)  # validate before changing
        new_terms = (
            self.payment_terms_days
            if payment_terms_days is None
            else _payment_terms(payment_terms_days)
        )
        new_tax_id = (
            self.tax_id if tax_id is KEEP else None if tax_id is None else compact_tax_id(tax_id)
        )
        self.name, self.payment_terms_days, self.tax_id = new_name, new_terms, new_tax_id
        self.tier = self.tier if tier is None else tier
        self.is_active = self.is_active if is_active is None else is_active
