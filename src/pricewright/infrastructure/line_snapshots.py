"""A priced line's snapshot in SQL (ADR-0019): quote lines and order lines store it alike.

The waterfall's steps are a JSON array, with decimals and ids as strings, as everywhere JSON carries
them (ADR-0003); the override is stored whole or not at all.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from pricewright.domain.money import Money
from pricewright.domain.orders import OrderLine
from pricewright.domain.pricing import (
    Adjustment,
    ManualOverride,
    MarginFloor,
    PriceBreakdown,
    PricedLine,
    PriceOverride,
    RateOverride,
    Stage,
)
from pricewright.domain.quotes import QuoteLine
from pricewright.infrastructure.records import OrderLineRecord, QuoteLineRecord

type _Step = dict[str, str | None]
type LineRecord = QuoteLineRecord | OrderLineRecord


def _optional(value: Decimal | UUID | None) -> str | None:
    return None if value is None else str(value)


def _step_json(step: Adjustment) -> _Step:
    return {
        "stage": step.stage.value,
        "rule_id": _optional(step.rule_id),
        "label": step.label,
        "rate": _optional(step.rate),
        "amount": str(step.amount.amount),
        "unit_price": str(step.unit_price.amount),
    }


def _step(data: _Step, currency: str) -> Adjustment:
    def money(key: str) -> Money:
        return Money(Decimal(str(data[key])), currency)

    rule_id, rate = data["rule_id"], data["rate"]
    return Adjustment(
        stage=Stage(str(data["stage"])),
        label=str(data["label"]),
        rule_id=None if rule_id is None else UUID(rule_id),
        rate=None if rate is None else Decimal(rate),
        amount=money("amount"),
        unit_price=money("unit_price"),
    )


def _override_values(override: ManualOverride | None) -> dict[str, object]:
    match override:
        case RateOverride(rate, reason):
            return {"override_kind": "rate", "override_rate": rate, "override_reason": reason}
        case PriceOverride(unit_price, reason):
            return {
                "override_kind": "price",
                "override_unit_price": unit_price.amount,
                "override_reason": reason,
            }
        case _:
            return {"override_kind": None}


# Column values for SQL statements: ``Any`` at this boundary, since each column has its own type.


def snapshot_values(line: QuoteLine | OrderLine, currency: str) -> dict[str, Any]:
    """The columns of a line's snapshot: the product as priced, the quantity, the engine's result
    and the override."""
    pricing, floor = line.pricing, line.pricing.margin_floor
    return {
        "product_id": line.product_id,
        "sku": line.sku,
        "product_name": line.product_name,
        "unit": line.unit.value,
        "quantity": line.quantity,
        "currency": currency,
        "list_unit_price": pricing.breakdown.list_unit_price.amount,
        "steps": [_step_json(step) for step in pricing.breakdown.steps],
        "list_total": pricing.list_total.amount,
        "net_total": pricing.net_total.amount,
        "cost_total": pricing.cost_total.amount,
        "margin_floor_rule_id": None if floor is None else floor.rule_id,
        "margin_floor_label": None if floor is None else floor.label,
        "margin_floor_rate": None if floor is None else floor.rate,
        "override_kind": None,
        "override_rate": None,
        "override_unit_price": None,
        "override_reason": None,
        **_override_values(line.override),
        "override_by": line.override_by,
    }


def priced_line(record: LineRecord) -> PricedLine:
    currency = record.currency
    floor = (
        None
        if record.margin_floor_rule_id is None
        or record.margin_floor_rate is None
        or record.margin_floor_label is None
        else MarginFloor(
            record.margin_floor_rule_id, record.margin_floor_label, record.margin_floor_rate
        )
    )
    return PricedLine(
        product_id=record.product_id,
        quantity=record.quantity,
        breakdown=PriceBreakdown(
            Money(record.list_unit_price, currency),
            tuple(_step(step, currency) for step in record.steps),
        ),
        list_total=Money(record.list_total, currency),
        net_total=Money(record.net_total, currency),
        cost_total=Money(record.cost_total, currency),
        margin_floor=floor,
    )


def override_of(record: LineRecord) -> ManualOverride | None:
    reason = record.override_reason or ""
    if record.override_kind == "rate" and record.override_rate is not None:
        return RateOverride(record.override_rate, reason)
    if record.override_kind == "price" and record.override_unit_price is not None:
        return PriceOverride(Money(record.override_unit_price, record.currency), reason)
    return None
