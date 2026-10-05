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
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from pricewright.domain.actors import Actor
from pricewright.domain.auth import PermissionDeniedError
from pricewright.domain.catalog import Product, UnitOfMeasure, UnknownProductError
from pricewright.domain.customers import Customer
from pricewright.domain.errors import ConflictError, NotFoundError, RuleViolationError
from pricewright.domain.money import Money
from pricewright.domain.orders import (
    CustomerSnapshot,
    Order,
    OrderLine,
    OrderTotals,
    customer_reference,
)
from pricewright.domain.pricing import (
    ApprovalReason,
    ArchivedCustomerError,
    LineRequest,
    ManualOverride,
    PricedLine,
    PricedQuote,
    price_quote,
)
from pricewright.domain.pricing_rules import PricingRule
from pricewright.domain.quote_approvals import ApprovalRequest, ApprovalStatus, SelfApprovalError
from pricewright.domain.quote_lifecycle import (
    QuoteAction,
    QuoteExpiredError,
    QuoteStatus,
    allowed_actions,
    has_passed,
    targets,
)
from pricewright.domain.tenants import TenantSettings
from pricewright.domain.updates import KEEP, Keep

MAX_QUOTE_LINES = 100
MAX_NOTES_LENGTH = 2000
MAX_REASON_LENGTH = 200


class InvalidQuoteError(RuleViolationError):
    code = "invalid_quote"


class QuoteNotEditableError(ConflictError):
    code = "quote_not_editable"


class UnknownQuoteLineError(NotFoundError):
    """A 404 like any other: the line is not on this quote."""


class EmptyQuoteError(ConflictError):
    code = "quote_empty"


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


def _reason(reason: str) -> str:
    text = reason.strip()
    if not text or len(text) > MAX_REASON_LENGTH:
        raise InvalidQuoteError(f"a cancellation needs a reason of 1 to {MAX_REASON_LENGTH} chars")
    return text


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
    approvals: list[ApprovalRequest] = field(default_factory=list)
    """Every approval request of this revision, oldest first; at most one pending."""
    submitted_by: Actor | None = None
    submitted_at: datetime | None = None
    cancel_reason: str | None = None
    supersedes_id: uuid.UUID | None = None
    """The revision this one replaced."""
    superseded_by_id: uuid.UUID | None = None
    """The revision that replaced this one (decision D-07)."""
    order_id: uuid.UUID | None = None
    """The order this revision became (ADR-0023)."""
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

    @property
    def pending_approval(self) -> ApprovalRequest | None:
        return next((a for a in self.approvals if a.status is ApprovalStatus.PENDING), None)

    def allowed_actions(self, now: datetime) -> frozenset[QuoteAction]:
        return allowed_actions(self.status, valid_until=self.valid_until, now=now)

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

    def submit(self, *, by: Actor, context: PricingContext) -> None:
        """Price one last time and freeze (ADR-0019); ask for approval when the quote needs it."""
        now = context.at
        targets(self.status, QuoteAction.SUBMIT, valid_until=self.valid_until, now=now)
        if not self.lines:
            raise EmptyQuoteError("add at least one line before submitting the quote")
        if has_passed(self.valid_until, now):
            raise QuoteExpiredError("valid_until has passed; set a new date before submitting")
        self.reprice(context)
        self.submitted_by, self.submitted_at = by, now
        if not self.approval_reasons:
            self._enter(QuoteStatus.APPROVED, now)
            return
        totals = self.totals
        self.approvals.append(
            ApprovalRequest(
                id=uuid.uuid7(),
                requested_by=by,
                requested_at=now,
                reasons=self.approval_reasons,
                discount=self.discount,
                approval_threshold=totals.approval_threshold,
                list_subtotal=totals.list_subtotal,
                net_subtotal=totals.net_subtotal,
            )
        )
        self._enter(QuoteStatus.PENDING_APPROVAL, now)

    def recall(self, *, now: datetime) -> None:
        """Withdraw the pending approval request; the quote is a draft again."""
        self._move(QuoteAction.RECALL, now)
        self._withdraw_pending(now)
        self.submitted_by = self.submitted_at = None

    def approve(self, *, by: Actor, comment: str | None = None, now: datetime) -> None:
        self._decide(ApprovalStatus.APPROVED, QuoteAction.APPROVE, by=by, comment=comment, now=now)

    def reject(self, *, by: Actor, comment: str, now: datetime) -> None:
        self._decide(ApprovalStatus.REJECTED, QuoteAction.REJECT, by=by, comment=comment, now=now)

    def send(self, *, now: datetime) -> None:
        self._move(QuoteAction.SEND, now)

    def accept(self, *, now: datetime) -> None:
        self._move(QuoteAction.ACCEPT, now)

    def cancel(self, *, reason: str, now: datetime) -> None:
        """Close the quote for good; also how a customer's "no" is recorded."""
        text = _reason(reason)
        self._move(QuoteAction.CANCEL, now)
        self._withdraw_pending(now)
        self.cancel_reason = text

    def revise(self, *, by: Actor, context: PricingContext) -> Quote:
        """Supersede this revision with a new draft: same number, the next revision, the same
        products, quantities and overrides priced again (ADR-0019), a fresh validity."""
        now = context.at
        targets(self.status, QuoteAction.REVISE, valid_until=self.valid_until, now=now)
        if context.customer.id != self.customer_id:
            raise ValueError("the pricing context is for another customer")
        copies = [
            _Line(
                uuid.uuid7(),
                line.product_id,
                line.quantity,
                line.added_by,
                line.override,
                line.override_by,
            )
            for line in self.lines
        ]
        quote_lines, totals = _priced(copies, context)
        today = now.astimezone(UTC).date()
        successor = Quote(
            id=uuid.uuid7(),
            tenant_id=self.tenant_id,
            number=self.number,
            revision=self.revision + 1,
            customer_id=self.customer_id,
            currency=self.currency,
            valid_until=today + timedelta(days=context.settings.quote_validity_days),
            created_by=by,
            created_at=now,
            totals=totals,
            lines=quote_lines,
            notes=self.notes,
            status_changed_at=now,
            supersedes_id=self.id,
        )
        self._move(QuoteAction.REVISE, now)
        self._withdraw_pending(now)
        self.superseded_by_id = successor.id
        return successor

    def convert(
        self,
        *,
        number: str,
        customer: Customer,
        by: Actor,
        now: datetime,
        reference: str | None = None,
    ) -> Order:
        """Become an order: this accepted revision's snapshot, copied unchanged (ADR-0023).

        An accepted quote converts after its ``valid_until``, since the customer accepted in time
        (ADR-0005); once converted, it never converts again.
        """
        targets(self.status, QuoteAction.CONVERT, valid_until=self.valid_until, now=now)
        if customer.id != self.customer_id:
            raise ValueError("the customer is not this quote's")
        if not customer.is_active:
            raise ArchivedCustomerError("an archived customer takes no new orders")
        totals = self.totals
        order = Order(
            id=uuid.uuid7(),
            tenant_id=self.tenant_id,
            number=number,
            quote_id=self.id,
            quote_number=self.display_number,
            customer=CustomerSnapshot.of(customer),
            currency=self.currency,
            totals=OrderTotals(
                list_subtotal=totals.list_subtotal,
                net_subtotal=totals.net_subtotal,
                tax_rate=totals.tax_rate,
                tax=totals.tax,
                total=totals.total,
                priced_at=totals.priced_at,
            ),
            created_by=by,
            created_at=now,
            lines=[
                OrderLine(
                    id=uuid.uuid7(),
                    product_id=line.product_id,
                    sku=line.sku,
                    product_name=line.product_name,
                    unit=line.unit,
                    quantity=line.quantity,
                    pricing=line.pricing,
                    override=line.override,
                    override_by=line.override_by,
                )
                for line in self.lines
            ],
            customer_reference=customer_reference(reference),
            status_changed_at=now,
        )
        self._move(QuoteAction.CONVERT, now)
        self.order_id = order.id
        return order

    def builders(self) -> frozenset[Actor]:
        """Everyone who shaped this revision: they never decide on its approval (ADR-0020)."""
        people = {self.created_by, *(line.added_by for line in self.lines)}
        people |= {Actor.person(line.override_by) for line in self.lines if line.override_by}
        if self.submitted_by is not None:
            people.add(self.submitted_by)
        return frozenset(people)

    def _decide(
        self,
        status: ApprovalStatus,
        action: QuoteAction,
        *,
        by: Actor,
        comment: str | None,
        now: datetime,
    ) -> None:
        (target,) = targets(self.status, action, valid_until=self.valid_until, now=now)
        if not by.is_person:
            raise PermissionDeniedError("only people decide on approvals")
        if by in self.builders():
            raise SelfApprovalError("someone who built or submitted this quote cannot decide on it")
        request = self.pending_approval
        if request is None:  # pragma: no cover - a quote pending approval always has its request
            raise RuntimeError("a quote pending approval has no pending request")
        request.decide(status, by=by, comment=comment, now=now)
        self._enter(target, now)

    def _move(self, action: QuoteAction, now: datetime) -> None:
        (target,) = targets(self.status, action, valid_until=self.valid_until, now=now)
        self._enter(target, now)

    def _enter(self, status: QuoteStatus, now: datetime) -> None:
        self.status, self.status_changed_at = status, now

    def _withdraw_pending(self, now: datetime) -> None:
        if (request := self.pending_approval) is not None:
            request.withdraw(now)

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
