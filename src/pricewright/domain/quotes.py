"""Quotes: the aggregate a rep builds, priced only by the pricing engine (ADR-0004).

A quote keeps a snapshot of what the engine computed for each line (list price, every step of the
waterfall, net and cost totals, the margin floor) and the quote's totals, so a stored quote never
depends on today's catalog or rules. While it is a draft, every change to its lines prices all of
them again with what is effective now; once submitted, nothing is recomputed. Statuses follow the
transition table of ADR-0005.
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from pricewright.domain.audit import ActorType
from pricewright.domain.auth import Principal
from pricewright.domain.catalog import Product, UnitOfMeasure, UnknownProductError
from pricewright.domain.customers import Customer
from pricewright.domain.errors import ConflictError, NotFoundError, RuleViolationError
from pricewright.domain.money import Money
from pricewright.domain.pricing import (
    ApprovalReason,
    LineRequest,
    ManualOverride,
    PricedLine,
    PricedQuote,
    price_quote,
)
from pricewright.domain.pricing_rules import PricingRule
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.tenants import TenantSettings
from pricewright.domain.updates import KEEP, Keep

MAX_QUOTE_LINES = 100
MAX_NOTES_LENGTH = 2000
NUMBER_SEQUENCE_DIGITS = 6


class InvalidQuoteError(RuleViolationError):
    code = "invalid_quote"


class QuoteNotEditableError(ConflictError):
    code = "quote_not_editable"


class UnknownQuoteLineError(NotFoundError):
    code = "quote_line_not_found"


def quote_number(prefix: str, year: int, sequence: int) -> str:
    """``NF-2026-000123``: the tenant's prefix, the year and the tenant's count in that year."""
    return f"{prefix}-{year}-{sequence:0{NUMBER_SEQUENCE_DIGITS}d}"


@dataclass(frozen=True, slots=True)
class Actor:
    """Who did something: a person or an integration."""

    type: ActorType
    id: uuid.UUID

    @classmethod
    def of(cls, principal: Principal) -> Actor:
        kind = ActorType.SERVICE_ACCOUNT if principal.is_service_account else ActorType.USER
        return cls(kind, principal.subject_id)


@dataclass(frozen=True, slots=True)
class PricingContext:
    """What pricing a quote reads. The use case loads it; the engine stays pure (ADR-0004)."""

    customer: Customer
    settings: TenantSettings
    rules: Sequence[PricingRule]
    products: Mapping[uuid.UUID, Product]
    """At least the products of every line being priced."""
    at: datetime

    def product(self, product_id: uuid.UUID) -> Product:
        product = self.products.get(product_id)
        if product is None:
            raise UnknownProductError(f"no such product in this tenant: {product_id}")
        return product


@dataclass(frozen=True, slots=True)
class LineChange:
    """What a rep asks for on a line; the price always comes from the engine."""

    product_id: uuid.UUID
    quantity: Decimal
    override: ManualOverride | None = None


@dataclass(frozen=True, slots=True)
class QuoteLine:
    """A product and quantity on a quote, with what the engine made of it."""

    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    product_name: str
    unit: UnitOfMeasure
    quantity: Decimal
    added_by: Actor
    pricing: PricedLine
    override: ManualOverride | None = None
    override_by: uuid.UUID | None = None
    """The person who set the override (brief §4, rule 1)."""


@dataclass(frozen=True, slots=True)
class QuoteTotals:
    """The quote's totals as the engine computed them at ``priced_at``."""

    list_subtotal: Money
    net_subtotal: Money
    tax_rate: Decimal
    tax: Money
    total: Money
    approval_threshold: Decimal
    priced_at: datetime


@dataclass(frozen=True, slots=True)
class _Line:
    """A line before pricing: what is kept from one pricing to the next."""

    id: uuid.UUID
    product_id: uuid.UUID
    quantity: Decimal
    added_by: Actor
    override: ManualOverride | None
    override_by: uuid.UUID | None

    @classmethod
    def new(cls, change: LineChange, by: Actor) -> _Line:
        setter = None if change.override is None else by.id
        return cls(uuid.uuid7(), change.product_id, change.quantity, by, change.override, setter)

    @classmethod
    def of(cls, line: QuoteLine) -> _Line:
        return cls(
            line.id, line.product_id, line.quantity, line.added_by, line.override, line.override_by
        )


def _priced(lines: Sequence[_Line], context: PricingContext) -> tuple[list[QuoteLine], QuoteTotals]:
    """Price the lines together, snapshotting each product as it is now."""
    products = [context.product(line.product_id) for line in lines]
    priced = price_quote(
        context.customer,
        [
            LineRequest(product, line.quantity, line.override)
            for line, product in zip(lines, products, strict=True)
        ],
        settings=context.settings,
        rules=context.rules,
        at=context.at,
    )
    quote_lines = [
        QuoteLine(
            id=line.id,
            product_id=product.id,
            sku=product.sku,
            product_name=product.name,
            unit=product.unit,
            quantity=pricing.quantity,
            added_by=line.added_by,
            pricing=pricing,
            override=line.override,
            override_by=line.override_by,
        )
        for line, product, pricing in zip(lines, products, priced.lines, strict=True)
    ]
    totals = QuoteTotals(
        list_subtotal=priced.list_subtotal,
        net_subtotal=priced.net_subtotal,
        tax_rate=priced.tax_rate,
        tax=priced.tax,
        total=priced.total,
        approval_threshold=priced.approval_threshold,
        priced_at=context.at,
    )
    return quote_lines, totals


def _notes(notes: str | None) -> str | None:
    if notes is None or not notes.strip():
        return None
    if len(notes) > MAX_NOTES_LENGTH:
        raise InvalidQuoteError(f"notes have at most {MAX_NOTES_LENGTH} characters")
    return notes.strip()


def _valid_until(valid_until: date, now: datetime) -> date:
    if valid_until < now.astimezone(UTC).date():
        raise InvalidQuoteError("valid_until cannot be in the past")
    return valid_until


@dataclass(slots=True)
class Quote:
    """One revision of a quote. ``version`` counts saved changes (optimistic concurrency, ADR-0012).

    Revisions of the same quote share its ``number``; revision 1 is shown without a suffix.
    """

    id: uuid.UUID
    tenant_id: uuid.UUID
    number: str
    revision: int
    customer_id: uuid.UUID
    currency: str
    valid_until: date
    created_by: Actor
    created_at: datetime
    totals: QuoteTotals
    lines: list[QuoteLine] = field(default_factory=list)
    notes: str | None = None
    status: QuoteStatus = QuoteStatus.DRAFT
    status_changed_at: datetime | None = None
    version: int = 1

    @classmethod
    def draft(
        cls,
        *,
        number: str,
        valid_until: date,
        by: Actor,
        context: PricingContext,
        notes: str | None = None,
        lines: Iterable[LineChange] = (),
    ) -> Quote:
        """A new draft for ``context.customer``, its lines priced at ``context.at``."""
        changes = list(lines)
        if len(changes) > MAX_QUOTE_LINES:
            raise InvalidQuoteError(f"a quote has at most {MAX_QUOTE_LINES} lines")
        quote_lines, totals = _priced([_Line.new(change, by) for change in changes], context)
        return cls(
            id=uuid.uuid7(),
            tenant_id=context.customer.tenant_id,
            number=number,
            revision=1,
            customer_id=context.customer.id,
            currency=context.settings.currency,
            valid_until=_valid_until(valid_until, context.at),
            created_by=by,
            created_at=context.at,
            totals=totals,
            lines=quote_lines,
            notes=_notes(notes),
            status_changed_at=context.at,
        )

    @property
    def display_number(self) -> str:
        """``NF-2026-000123``, then ``NF-2026-000123-R2`` for later revisions."""
        return self.number if self.revision == 1 else f"{self.number}-R{self.revision}"

    @property
    def priced(self) -> PricedQuote:
        """The stored snapshot as the engine's result, for the discount and approval reasons."""
        totals = self.totals
        return PricedQuote(
            lines=tuple(line.pricing for line in self.lines),
            list_subtotal=totals.list_subtotal,
            net_subtotal=totals.net_subtotal,
            tax_rate=totals.tax_rate,
            tax=totals.tax,
            total=totals.total,
            approval_threshold=totals.approval_threshold,
        )

    @property
    def discount(self) -> Decimal:
        return self.priced.discount

    @property
    def approval_reasons(self) -> tuple[ApprovalReason, ...]:
        return self.priced.approval_reasons

    def line(self, line_id: uuid.UUID) -> QuoteLine:
        for line in self.lines:
            if line.id == line_id:
                return line
        raise UnknownQuoteLineError("no such line on this quote")

    def add_line(self, change: LineChange, *, by: Actor, context: PricingContext) -> QuoteLine:
        """Add a line and price the whole quote again with what is effective now."""
        self._require_draft()
        if len(self.lines) >= MAX_QUOTE_LINES:
            raise InvalidQuoteError(f"a quote has at most {MAX_QUOTE_LINES} lines")
        added = _Line.new(change, by)
        self._price([*map(_Line.of, self.lines), added], context)
        return self.line(added.id)

    def change_line(
        self,
        line_id: uuid.UUID,
        *,
        quantity: Decimal | None = None,
        override: ManualOverride | Keep | None = KEEP,
        by: Actor,
        context: PricingContext,
    ) -> QuoteLine:
        """Change a line's quantity or override (``None`` removes it), then price everything."""
        self._require_draft()
        current = _Line.of(self.line(line_id))
        changed = _Line(
            current.id,
            current.product_id,
            current.quantity if quantity is None else quantity,
            current.added_by,
            current.override if override is KEEP else override,
            current.override_by if override is KEEP else (None if override is None else by.id),
        )
        lines = [changed if line.id == line_id else _Line.of(line) for line in self.lines]
        self._price(lines, context)
        return self.line(line_id)

    def remove_line(self, line_id: uuid.UUID, *, context: PricingContext) -> QuoteLine:
        """Remove a line and price the rest again."""
        self._require_draft()
        removed = self.line(line_id)
        self._price([_Line.of(line) for line in self.lines if line.id != line_id], context)
        return removed

    def change_terms(
        self, *, valid_until: date | None = None, notes: str | Keep | None = KEEP, now: datetime
    ) -> None:
        """Terms do not change prices, so nothing is priced again."""
        self._require_draft()
        new_valid_until = (
            self.valid_until if valid_until is None else _valid_until(valid_until, now)
        )
        new_notes = self.notes if notes is KEEP else _notes(notes)
        self.valid_until, self.notes = new_valid_until, new_notes

    def reprice(self, context: PricingContext) -> None:
        """Price every line again with what is effective at ``context.at``."""
        self._price([_Line.of(line) for line in self.lines], context)

    def _require_draft(self) -> None:
        if self.status is not QuoteStatus.DRAFT:
            raise QuoteNotEditableError(
                f"only drafts change; this quote is {self.status.value.replace('_', ' ')}"
            )

    def _price(self, lines: Sequence[_Line], context: PricingContext) -> None:
        """Store the lines and totals priced together; on any error nothing changes."""
        if context.customer.id != self.customer_id:
            raise ValueError("the pricing context is for another customer")
        self.lines, self.totals = _priced(lines, context)
