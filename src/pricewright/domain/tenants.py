"""Tenants: the distributor companies that use Pricewright, and the settings pricing depends on."""

import re
import uuid
from dataclasses import dataclass
from decimal import Decimal

from pricewright.domain.errors import RuleViolationError

DEFAULT_APPROVAL_THRESHOLD = Decimal("0.15")
# Rates are stored as NUMERIC(5, 4): 0.0725 is 7.25%. More places would be rounded silently.
RATE_DECIMAL_PLACES = 4
MAX_NAME_LENGTH = 200
_CURRENCY_CODE = re.compile(r"[A-Z]{3}")


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
    """Commercial settings read by pricing (tax) and approvals (discount threshold)."""

    currency: str
    tax_rate: Decimal
    approval_threshold: Decimal = DEFAULT_APPROVAL_THRESHOLD

    def __post_init__(self) -> None:
        if not _CURRENCY_CODE.fullmatch(self.currency):
            raise InvalidTenantError("currency must be an ISO 4217 code such as USD")
        _require_rate(self.tax_rate, "tax_rate", low=Decimal(0), high=Decimal(1), high_ok=False)
        _require_rate(
            self.approval_threshold,
            "approval_threshold",
            low=Decimal(0),
            high=Decimal(1),
            high_ok=True,
        )


@dataclass(slots=True)
class Tenant:
    """A distributor company. Every business record belongs to exactly one tenant."""

    id: uuid.UUID
    name: str
    settings: TenantSettings

    @classmethod
    def register(cls, *, name: str, settings: TenantSettings) -> Tenant:
        name = name.strip()
        if not name or len(name) > MAX_NAME_LENGTH:
            raise InvalidTenantError(f"name must have 1 to {MAX_NAME_LENGTH} characters")
        return cls(id=uuid.uuid7(), name=name, settings=settings)
