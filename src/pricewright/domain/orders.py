"""Orders: what a customer committed to, converted once from an accepted quote (ADR-0023).

An order is a document of its own, as in SAP, Salesforce CPQ, Dynamics 365 and Stripe. It copies
the accepted quote's snapshot unchanged (SAP's "copy pricing elements unchanged", Dynamics 365's
locked prices): every line with its waterfall, the totals, the tax rate and amounts (decision D-09).
Nothing is priced again. It also keeps the customer as it was when the order was placed, as an
invoice keeps its customer's details once finalized. Fulfilment, invoicing and payments are out of
scope (brief §10): an open order only changes to be cancelled.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pricewright.domain.actors import Actor
from pricewright.domain.catalog import UnitOfMeasure
from pricewright.domain.customers import Customer
from pricewright.domain.errors import RuleViolationError
from pricewright.domain.money import Money
from pricewright.domain.pricing import ManualOverride, PricedLine
from pricewright.domain.quote_lifecycle import InvalidTransitionError

MAX_CUSTOMER_REFERENCE_LENGTH = 35  # SAP's customer purchase order number (BSTKD)
MAX_CANCEL_REASON_LENGTH = 200


class InvalidOrderError(RuleViolationError):
    code = "invalid_order"


class OrderStatus(StrEnum):
    """Fulfilment and invoicing would add statuses; clients handle unknown ones (ADR-0015)."""

    OPEN = "open"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class CustomerSnapshot:
    """The customer as the order was placed; it stays so whatever later happens to the customer."""

    id: uuid.UUID
    account_number: str
    name: str
    tax_id: str | None
    payment_terms_days: int
    """Net days, from the customer: quotes carry prices, not payment terms."""

    @classmethod
    def of(cls, customer: Customer) -> CustomerSnapshot:
        return cls(
            id=customer.id,
            account_number=customer.account_number,
            name=customer.name,
            tax_id=customer.tax_id,
            payment_terms_days=customer.payment_terms_days,
        )


@dataclass(frozen=True, slots=True)
class OrderLine:
    """A quote line as accepted: the product as it was priced and the engine's result."""

    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    product_name: str
    unit: UnitOfMeasure
    quantity: Decimal
    pricing: PricedLine
    override: ManualOverride | None = None
    override_by: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class OrderTotals:
    """The accepted quote's totals; tax at the rate the quote was priced with (decision D-09)."""

    list_subtotal: Money
    net_subtotal: Money
    tax_rate: Decimal
    tax: Money
    total: Money
    priced_at: datetime
    """When the quote's prices were set, at its submission."""


def customer_reference(text: str | None) -> str | None:
    """The customer's own reference for the order, such as its purchase order number."""
    if text is None or not text.strip():
        return None
    if len(text.strip()) > MAX_CUSTOMER_REFERENCE_LENGTH:
        raise InvalidOrderError(
            f"a customer reference has at most {MAX_CUSTOMER_REFERENCE_LENGTH} characters"
        )
    return text.strip()


def _reason(reason: str) -> str:
    text = reason.strip()
    if not text or len(text) > MAX_CANCEL_REASON_LENGTH:
        raise InvalidOrderError(
            f"a cancellation needs a reason of 1 to {MAX_CANCEL_REASON_LENGTH} characters"
        )
    return text


@dataclass(slots=True)
class Order:
    """A customer's order. ``version`` counts saved changes (optimistic concurrency, ADR-0012).

    Created only by converting an accepted quote (``Quote.convert``).
    """

    id: uuid.UUID
    tenant_id: uuid.UUID
    number: str
    quote_id: uuid.UUID
    quote_number: str
    """The accepted revision's number as people read it: ``NF-2026-000123-R2``."""
    customer: CustomerSnapshot
    currency: str
    totals: OrderTotals
    created_by: Actor
    created_at: datetime
    lines: list[OrderLine] = field(default_factory=list)
    customer_reference: str | None = None
    status: OrderStatus = OrderStatus.OPEN
    status_changed_at: datetime | None = None
    cancel_reason: str | None = None
    version: int = 1

    def cancel(self, *, reason: str, now: datetime) -> None:
        """Withdraw the commitment, with a reason; the order stays, as every document does."""
        text = _reason(reason)
        if self.status is not OrderStatus.OPEN:
            raise InvalidTransitionError(f"cannot cancel an order that is {self.status.value}")
        self.status, self.status_changed_at, self.cancel_reason = OrderStatus.CANCELLED, now, text
