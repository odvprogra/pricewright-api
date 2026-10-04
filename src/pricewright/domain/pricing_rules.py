"""Pricing rules: the discounts and margin floors the pricing engine applies (ADR-0018).

One record per rule, as Oracle's modifiers and Dynamics 365's trade agreements keep them: a kind, a
scope (one product, one category or every product), a customer tier for tier discounts, and a
validity window. Volume tiers hold their quantity brackets. Rules are deactivated or end-dated,
never deleted, and their kind, scope and tier never change: like SAP's condition records, a rule
for something else is a new rule.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pricewright.domain.catalog import Product
from pricewright.domain.customers import CustomerTier
from pricewright.domain.errors import RuleViolationError
from pricewright.domain.quantities import quantity
from pricewright.domain.updates import KEEP, Keep

MAX_RULE_NAME_LENGTH = 100
MAX_BRACKETS = 10
RATE_DECIMAL_PLACES = 4  # NUMERIC(5, 4), like the tenant's rates: 0.1250 is 12.5%


class InvalidPricingRuleError(RuleViolationError):
    code = "invalid_pricing_rule"


class RuleKind(StrEnum):
    """The discount kinds are the stages of the price waterfall, in this order (ADR-0004)."""

    VOLUME_TIER = "volume_tier"
    CUSTOMER_TIER = "customer_tier"
    PROMOTION = "promotion"
    MARGIN_FLOOR = "margin_floor"
    """Not a discount: the least margin a line may keep without needing approval."""


@dataclass(frozen=True, slots=True)
class Bracket:
    """From ``min_quantity`` units on, the whole line gets ``rate`` off (Salesforce CPQ's "range"
    schedules, SAP's scales)."""

    min_quantity: Decimal
    rate: Decimal


def _has_rate_places(value: Decimal) -> bool:
    exponent = value.as_tuple().exponent
    return isinstance(exponent, int) and -exponent <= RATE_DECIMAL_PLACES


def discount_rate(value: Decimal) -> Decimal:
    """More than nothing off, up to everything off: 0.1 is 10%."""
    if not _has_rate_places(value) or not 0 < value <= 1:
        raise InvalidPricingRuleError(
            "a discount is more than 0 and at most 1 (everything off), with at most "
            f"{RATE_DECIMAL_PLACES} decimal places: 0.125 is 12.5%"
        )
    return value


def _floor_rate(value: Decimal) -> Decimal:
    if not _has_rate_places(value) or not 0 <= value < 1:
        raise InvalidPricingRuleError(
            "a margin floor is at least 0 and less than 1, with at most "
            f"{RATE_DECIMAL_PLACES} decimal places: 0.2 keeps a 20% margin"
        )
    return value


def _brackets(brackets: Sequence[Bracket]) -> tuple[Bracket, ...]:
    if not 1 <= len(brackets) <= MAX_BRACKETS:
        raise InvalidPricingRuleError(f"a volume tier has 1 to {MAX_BRACKETS} brackets")
    checked = sorted(
        (Bracket(quantity(b.min_quantity), discount_rate(b.rate)) for b in brackets),
        key=lambda bracket: bracket.min_quantity,
    )
    if len({bracket.min_quantity for bracket in checked}) < len(checked):
        raise InvalidPricingRuleError("two brackets of a volume tier start at the same quantity")
    return tuple(checked)


def _name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > MAX_RULE_NAME_LENGTH:
        raise InvalidPricingRuleError(f"a rule name has 1 to {MAX_RULE_NAME_LENGTH} characters")
    return name


def _terms(
    kind: RuleKind, rate: Decimal | None, brackets: Sequence[Bracket]
) -> tuple[Decimal | None, tuple[Bracket, ...]]:
    if kind is RuleKind.VOLUME_TIER:
        if rate is not None:
            raise InvalidPricingRuleError("a volume tier's rates are in its brackets")
        return None, _brackets(brackets)
    if brackets:
        raise InvalidPricingRuleError("only volume tiers have brackets")
    if rate is None:
        raise InvalidPricingRuleError(f"a {kind} rule needs a rate")
    return (_floor_rate(rate) if kind is RuleKind.MARGIN_FLOOR else discount_rate(rate)), ()


def _window(kind: RuleKind, valid_from: datetime, valid_to: datetime | None) -> None:
    if valid_from.tzinfo is None or (valid_to is not None and valid_to.tzinfo is None):
        raise InvalidPricingRuleError("validity times carry their time zone (UTC)")
    if valid_to is not None and valid_to <= valid_from:
        raise InvalidPricingRuleError("valid_to comes after valid_from")
    if kind is RuleKind.PROMOTION and valid_to is None:
        raise InvalidPricingRuleError("a promotion is time-boxed: it needs valid_to")


@dataclass(slots=True)
class PricingRule:
    """A discount or a margin floor for one product, one category or every product.

    It applies from ``valid_from`` (inclusive) until ``valid_to`` (exclusive), or indefinitely.
    """

    id: uuid.UUID
    tenant_id: uuid.UUID
    kind: RuleKind
    name: str
    valid_from: datetime
    valid_to: datetime | None = None
    rate: Decimal | None = None
    """The discount, or a margin floor's least margin; None for volume tiers (in the brackets)."""
    brackets: tuple[Bracket, ...] = ()
    product_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    customer_tier: CustomerTier | None = None
    is_active: bool = True
    version: int = 1
    """Counts saved changes; the repository bumps it (ADR-0012)."""

    @classmethod
    def create(
        cls,
        *,
        tenant_id: uuid.UUID,
        kind: RuleKind,
        name: str,
        valid_from: datetime,
        valid_to: datetime | None = None,
        rate: Decimal | None = None,
        brackets: Sequence[Bracket] = (),
        product_id: uuid.UUID | None = None,
        category_id: uuid.UUID | None = None,
        customer_tier: CustomerTier | None = None,
    ) -> PricingRule:
        if product_id is not None and category_id is not None:
            raise InvalidPricingRuleError(
                "a rule applies to one product, one category or every product"
            )
        if (customer_tier is None) is (kind is RuleKind.CUSTOMER_TIER):
            raise InvalidPricingRuleError("customer tier discounts, and only they, name a tier")
        _window(kind, valid_from, valid_to)
        rate, checked_brackets = _terms(kind, rate, brackets)
        return cls(
            id=uuid.uuid7(),
            tenant_id=tenant_id,
            kind=kind,
            name=_name(name),
            valid_from=valid_from,
            valid_to=valid_to,
            rate=rate,
            brackets=checked_brackets,
            product_id=product_id,
            category_id=category_id,
            customer_tier=customer_tier,
        )

    def change(
        self,
        *,
        name: str | None = None,
        rate: Decimal | None = None,
        brackets: Sequence[Bracket] | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | Keep | None = KEEP,
        is_active: bool | None = None,
    ) -> None:
        """Fields left as ``None`` (``KEEP`` for ``valid_to``) keep their value; ``valid_to=None``
        removes the end. Kind, scope and tier are the rule's identity and do not change."""
        new_name = self.name if name is None else _name(name)  # validate before changing
        new_from = self.valid_from if valid_from is None else valid_from
        new_to = self.valid_to if valid_to is KEEP else valid_to
        _window(self.kind, new_from, new_to)
        new_rate, new_brackets = _terms(
            self.kind,
            self.rate if rate is None else rate,
            self.brackets if brackets is None else brackets,
        )
        self.name, self.valid_from, self.valid_to = new_name, new_from, new_to
        self.rate, self.brackets = new_rate, new_brackets
        self.is_active = self.is_active if is_active is None else is_active

    def is_effective(self, at: datetime) -> bool:
        """Active and within its window: a rule that ends at midnight no longer applies then."""
        if not self.is_active or at < self.valid_from:
            return False
        return self.valid_to is None or at < self.valid_to

    def covers(self, product: Product, tier: CustomerTier) -> bool:
        """Whether the rule is about this product, for a customer of this tier."""
        if self.customer_tier is not None and self.customer_tier != tier:
            return False
        if self.product_id is not None:
            return product.id == self.product_id
        return self.category_id is None or product.category_id == self.category_id

    @property
    def specificity(self) -> int:
        """2 for a product, 1 for a category, 0 for every product: the order in which SAP's access
        sequences search for a condition record."""
        return 2 if self.product_id is not None else 1 if self.category_id is not None else 0

    def rate_for(self, units: Decimal) -> Decimal | None:
        """The rate on a line of ``units``: a volume tier's highest bracket reached, if any."""
        if self.kind is not RuleKind.VOLUME_TIER:
            return self.rate
        reached = [bracket.rate for bracket in self.brackets if bracket.min_quantity <= units]
        return reached[-1] if reached else None
