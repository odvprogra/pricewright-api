"""Tenants: the distributor companies that use Pricewright, and the settings pricing depends on."""

import re
import uuid
from dataclasses import dataclass, replace
from decimal import Decimal

from pricewright.domain.currencies import is_iso_4217
from pricewright.domain.errors import RuleViolationError

DEFAULT_APPROVAL_THRESHOLD = Decimal("0.15")
# Rates are stored as NUMERIC(5, 4): 0.0725 is 7.25%. More places would be rounded silently.
RATE_DECIMAL_PLACES = 4
MAX_NAME_LENGTH = 200
# Dynamics 365 Sales numbers quotes with the prefix QUO until an administrator changes it.
DEFAULT_QUOTE_PREFIX = "QUO"
MAX_QUOTE_PREFIX_LENGTH = 5
# A letter, then letters or digits: no hyphen, which separates the parts of a quote number.
QUOTE_PREFIX_PATTERN = re.compile(r"[A-Z][A-Z0-9]{1,4}")
# Odoo and Salesforce CPQ propose a quote's expiration from a default validity in days.
DEFAULT_QUOTE_VALIDITY_DAYS = 30
MAX_QUOTE_VALIDITY_DAYS = 365


class InvalidTenantError(RuleViolationError):
    code = "invalid_tenant"


def _require_rate(value: Decimal, name: str, *, low: Decimal, high: Decimal, high_ok: bool) -> None:
    exponent = value.as_tuple().exponent
    if not value.is_finite() or not isinstance(exponent, int) or -exponent > RATE_DECIMAL_PLACES:
        raise InvalidTenantError(f"{name} must be a number with at most 4 decimal places")
    if value < low or value > high or (value == high and not high_ok):
        closing = "]" if high_ok else ")"
        raise InvalidTenantError(f"{name} must be in [{low}, {high}{closing}")


@dataclass(frozen=True, slots=True)
class TenantSettings:
    """Commercial settings read by pricing (tax), approvals (discount threshold) and quotes (number
    prefix, default validity)."""

    currency: str
    tax_rate: Decimal
    approval_threshold: Decimal = DEFAULT_APPROVAL_THRESHOLD
    quote_prefix: str = DEFAULT_QUOTE_PREFIX
    """Starts every quote number: ``NF`` gives ``NF-2026-000123``."""
    quote_validity_days: int = DEFAULT_QUOTE_VALIDITY_DAYS
    """How long a new quote is valid unless the rep sets another date."""

    def __post_init__(self) -> None:
        if not is_iso_4217(self.currency):
            raise InvalidTenantError("currency must be an ISO 4217 code such as USD")
        _require_rate(self.tax_rate, "tax_rate", low=Decimal(0), high=Decimal(1), high_ok=False)
        _require_rate(
            self.approval_threshold,
            "approval_threshold",
            low=Decimal(0),
            high=Decimal(1),
            high_ok=True,
        )
        if not QUOTE_PREFIX_PATTERN.fullmatch(self.quote_prefix):
            raise InvalidTenantError(
                "quote_prefix must be 2 to 5 upper-case letters or digits, starting with a letter"
            )
        if not 1 <= self.quote_validity_days <= MAX_QUOTE_VALIDITY_DAYS:
            raise InvalidTenantError(
                f"quote_validity_days must be from 1 to {MAX_QUOTE_VALIDITY_DAYS}"
            )


def _valid_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        raise InvalidTenantError(f"name must have 1 to {MAX_NAME_LENGTH} characters")
    return name


@dataclass(slots=True)
class Tenant:
    """A distributor company. Every business record belongs to exactly one tenant.

    ``version`` counts saved changes; the repository bumps it (optimistic concurrency, ADR-0012).
    """

    id: uuid.UUID
    name: str
    settings: TenantSettings
    version: int = 1

    @classmethod
    def register(cls, *, name: str, settings: TenantSettings) -> Tenant:
        return cls(id=uuid.uuid7(), name=_valid_name(name), settings=settings)

    def change(
        self,
        *,
        name: str | None = None,
        tax_rate: Decimal | None = None,
        approval_threshold: Decimal | None = None,
        quote_prefix: str | None = None,
        quote_validity_days: int | None = None,
    ) -> None:
        """Rename, adjust rates or quote settings. The currency is fixed: every stored price is in
        it. A new quote prefix numbers new quotes only; issued numbers never change."""
        current = self.settings
        settings = replace(
            current,
            tax_rate=current.tax_rate if tax_rate is None else tax_rate,
            approval_threshold=(
                current.approval_threshold if approval_threshold is None else approval_threshold
            ),
            quote_prefix=current.quote_prefix if quote_prefix is None else quote_prefix,
            quote_validity_days=(
                current.quote_validity_days if quote_validity_days is None else quote_validity_days
            ),
        )
        self.name = self.name if name is None else _valid_name(name)
        self.settings = settings
