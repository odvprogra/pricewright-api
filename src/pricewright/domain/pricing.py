"""The pricing engine: a quote's lines priced through the waterfall, and why (ADR-0004).

List price → volume tier → customer tier → promotion → manual override, then the margin floor guard
(brief §4). Each stage takes at most one discount, the best for the customer, off the unit price the
previous stage left, rounded to four places half up; line totals, tax and the total round to the
currency's minor units (ADR-0003). Pure: rules, customer and products in, priced lines out.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pricewright.domain.catalog import Product
from pricewright.domain.customers import Customer, CustomerTier
from pricewright.domain.errors import RuleViolationError
from pricewright.domain.money import AMOUNT_DECIMAL_PLACES, Money, round_half_up
from pricewright.domain.pricing_rules import (
    InvalidPricingRuleError,
    PricingRule,
    RuleKind,
    discount_rate,
)
from pricewright.domain.quantities import quantity
from pricewright.domain.tenants import TenantSettings

MAX_REASON_LENGTH = 200
RATIO_DECIMAL_PLACES = 4


class InvalidOverrideError(RuleViolationError):
    code = "invalid_override"


class ArchivedProductError(RuleViolationError):
    code = "product_archived"


class ArchivedCustomerError(RuleViolationError):
    code = "customer_archived"


class Stage(StrEnum):
    """The steps of the waterfall, in order. Rules supply the first three."""

    VOLUME_TIER = "volume_tier"
    CUSTOMER_TIER = "customer_tier"
    PROMOTION = "promotion"
    MANUAL_OVERRIDE = "manual_override"


_RULE_STAGES = (RuleKind.VOLUME_TIER, RuleKind.CUSTOMER_TIER, RuleKind.PROMOTION)


def _reason(reason: str) -> None:
    if not reason.strip() or len(reason) > MAX_REASON_LENGTH:
        raise InvalidOverrideError(f"an override needs a reason of 1 to {MAX_REASON_LENGTH} chars")


@dataclass(frozen=True, slots=True)
class RateOverride:
    """A manager's extra discount off the price the rules left, with a reason (brief §4, rule 1)."""

    rate: Decimal
    reason: str

    def __post_init__(self) -> None:
        _reason(self.reason)
        try:
            discount_rate(self.rate)
        except InvalidPricingRuleError as error:
            raise InvalidOverrideError(str(error)) from error


@dataclass(frozen=True, slots=True)
class PriceOverride:
    """A manager's net unit price, replacing what the rules left, with a reason."""

    unit_price: Money
    reason: str

    def __post_init__(self) -> None:
        _reason(self.reason)
        if self.unit_price.amount < 0:
            raise InvalidOverrideError("an override's unit price cannot be negative")


type ManualOverride = RateOverride | PriceOverride
"""Salesforce CPQ's manual discounts and custom prices; always the last step (ADR-0004)."""


@dataclass(frozen=True, slots=True)
class LineRequest:
    product: Product
    quantity: Decimal
    override: ManualOverride | None = None


@dataclass(frozen=True, slots=True)
class Adjustment:
    """One step of the waterfall: what a stage did to the unit price, and why."""

    stage: Stage
    label: str
    """The rule's name, or the override's reason."""
    rule_id: uuid.UUID | None
    """None for a manual override."""
    rate: Decimal | None
    """The rate off; None when an override set the price itself."""
    amount: Money
    """Per unit; negative when it takes something off."""
    unit_price: Money
    """The unit price after this step."""


@dataclass(frozen=True, slots=True)
class PriceBreakdown:
    """Why a unit costs what it costs: its list price and each step that changed it."""

    list_unit_price: Money
    steps: tuple[Adjustment, ...]

    @property
    def net_unit_price(self) -> Money:
        return self.steps[-1].unit_price if self.steps else self.list_unit_price


@dataclass(frozen=True, slots=True)
class MarginFloor:
    rule_id: uuid.UUID
    label: str
    rate: Decimal
    """The least gross margin on the selling price the line may keep without approval."""


@dataclass(frozen=True, slots=True)
class PricedLine:
    product_id: uuid.UUID
    quantity: Decimal
    breakdown: PriceBreakdown
    list_total: Money
    net_total: Money
    cost_total: Money
    margin_floor: MarginFloor | None

    @property
    def margin(self) -> Money:
        return self.net_total - self.cost_total

    @property
    def margin_rate(self) -> Decimal | None:
        """Gross margin on the selling price, to four places; None for a line given away."""
        if self.net_total.amount == 0:
            return None
        return round_half_up(self.margin.amount / self.net_total.amount, RATIO_DECIMAL_PLACES)

    @property
    def below_margin_floor(self) -> bool:
        floor = self.margin_floor
        return floor is not None and self.margin.amount < floor.rate * self.net_total.amount


class ApprovalReason(StrEnum):
    DISCOUNT_ABOVE_THRESHOLD = "discount_above_threshold"
    LINE_BELOW_MARGIN_FLOOR = "line_below_margin_floor"


@dataclass(frozen=True, slots=True)
class PricedQuote:
    lines: tuple[PricedLine, ...]
    list_subtotal: Money
    net_subtotal: Money
    tax_rate: Decimal
    tax: Money
    total: Money
    approval_threshold: Decimal

    @property
    def discount(self) -> Decimal:
        """The value-weighted discount, 1 - net / list subtotal, before tax and with overrides
        (decision D-06), to four places."""
        if self.list_subtotal.amount == 0:
            return Decimal(0)
        ratio = self.net_subtotal.amount / self.list_subtotal.amount
        return round_half_up(1 - ratio, RATIO_DECIMAL_PLACES)

    @property
    def approval_reasons(self) -> tuple[ApprovalReason, ...]:
        reasons: list[ApprovalReason] = []
        # Exact: the discount exceeds the threshold when net < list x (1 - threshold).
        allowed = self.list_subtotal.amount * (1 - self.approval_threshold)
        if self.net_subtotal.amount < allowed:
            reasons.append(ApprovalReason.DISCOUNT_ABOVE_THRESHOLD)
        if any(line.below_margin_floor for line in self.lines):
            reasons.append(ApprovalReason.LINE_BELOW_MARGIN_FLOOR)
        return tuple(reasons)

    @property
    def requires_approval(self) -> bool:
        return bool(self.approval_reasons)


def price_quote(
    customer: Customer,
    lines: Sequence[LineRequest],
    *,
    settings: TenantSettings,
    rules: Iterable[PricingRule],
    at: datetime,
) -> PricedQuote:
    """Price every line with the rules effective ``at``, then total the quote."""
    if not customer.is_active:
        raise ArchivedCustomerError(f"customer {customer.account_number} is archived")
    effective = [rule for rule in rules if rule.is_effective(at)]
    priced = tuple(_price_line(line, customer.tier, effective) for line in lines)
    zero = Money(Decimal(0), settings.currency)
    net_subtotal = sum((line.net_total for line in priced), zero)
    tax = Money(
        round_half_up(net_subtotal.amount * settings.tax_rate, zero.minor_units), zero.currency
    )
    return PricedQuote(
        lines=priced,
        list_subtotal=sum((line.list_total for line in priced), zero),
        net_subtotal=net_subtotal,
        tax_rate=settings.tax_rate,
        tax=tax,
        total=net_subtotal + tax,
        approval_threshold=settings.approval_threshold,
    )


def _price_line(line: LineRequest, tier: CustomerTier, rules: Sequence[PricingRule]) -> PricedLine:
    product = line.product
    if not product.is_active:
        raise ArchivedProductError(f"product {product.sku} is archived")
    units = quantity(line.quantity)
    covering = [rule for rule in rules if rule.covers(product, tier)]
    steps: list[Adjustment] = []
    price = product.list_price
    for kind in _RULE_STAGES:
        best = _best_discount(kind, covering, units)
        if best is not None:
            rule, rate = best
            steps.append(_discounted(Stage(kind), price, rate, label=rule.name, rule_id=rule.id))
            price = steps[-1].unit_price
    if line.override is not None:
        steps.append(_overridden(price, line.override))
        price = steps[-1].unit_price
    return PricedLine(
        product_id=product.id,
        quantity=units,
        breakdown=PriceBreakdown(product.list_price, tuple(steps)),
        list_total=_line_total(product.list_price, units),
        net_total=_line_total(price, units),
        cost_total=_line_total(product.unit_cost, units),
        margin_floor=_margin_floor(covering),
    )


def _best_discount(
    kind: RuleKind, rules: Iterable[PricingRule], units: Decimal
) -> tuple[PricingRule, Decimal] | None:
    """The highest rate; a tie goes to the most specific rule, then the oldest (ADR-0018)."""
    candidates = [
        (rule, rate)
        for rule in rules
        if rule.kind is kind and (rate := rule.rate_for(units)) is not None
    ]
    if not candidates:
        return None
    rule, rate = min(candidates, key=lambda pair: (-pair[1], -pair[0].specificity, pair[0].id))
    return rule, rate


def _margin_floor(rules: Iterable[PricingRule]) -> MarginFloor | None:
    """The most specific floor, so a category may allow less than the tenant-wide floor; among
    equally specific ones, the highest (ADR-0018)."""
    floors = [
        (rule, rate)
        for rule in rules
        if rule.kind is RuleKind.MARGIN_FLOOR and (rate := rule.rate) is not None
    ]
    if not floors:
        return None
    rule, rate = min(floors, key=lambda floor: (-floor[0].specificity, -floor[1], floor[0].id))
    return MarginFloor(rule.id, rule.name, rate)


def _discounted(
    stage: Stage, price: Money, rate: Decimal, *, label: str, rule_id: uuid.UUID | None
) -> Adjustment:
    after = Money(round_half_up(price.amount * (1 - rate), AMOUNT_DECIMAL_PLACES), price.currency)
    return Adjustment(stage, label, rule_id, rate, amount=after - price, unit_price=after)


def _overridden(price: Money, override: ManualOverride) -> Adjustment:
    if isinstance(override, RateOverride):
        return _discounted(
            Stage.MANUAL_OVERRIDE, price, override.rate, label=override.reason, rule_id=None
        )
    target = override.unit_price
    if target.currency != price.currency:
        raise InvalidOverrideError(f"an override's unit price must be in {price.currency}")
    return Adjustment(
        Stage.MANUAL_OVERRIDE,
        override.reason,
        rule_id=None,
        rate=None,
        amount=target - price,
        unit_price=target,
    )


def _line_total(unit_price: Money, units: Decimal) -> Money:
    return Money(
        round_half_up(unit_price.amount * units, unit_price.minor_units), unit_price.currency
    )
