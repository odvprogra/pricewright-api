"""Quotes: reps, managers and integrations build them; the pricing engine prices them (ADR-0019).

Every use case binds the unit of work to the caller's tenant (ADR-0006), saves with the version the
caller read (ADR-0012) and records its audit event in the same unit of work (ADR-0013).
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from pricewright.application.audit import quote_fields, quote_line_fields, record
from pricewright.application.pagination import Keyset, Page, page_of
from pricewright.application.ports import (
    Clock,
    QuoteQuery,
    QuoteSort,
    QuoteSummary,
    UnitOfWork,
    UnitOfWorkFactory,
)
from pricewright.application.pricing import pricing_context, tenant_of
from pricewright.domain.actors import Actor
from pricewright.domain.audit import AuditAction, changed, created, removed
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.pricing import ManualOverride
from pricewright.domain.quotes import LineChange, PricingContext, Quote, quote_number
from pricewright.domain.updates import KEEP, Keep


@dataclass(frozen=True, slots=True)
class NewQuote:
    customer_id: UUID
    valid_until: date | None = None
    """Defaults to today plus the tenant's quote validity."""
    notes: str | None = None
    lines: Sequence[LineChange] = field(default_factory=tuple)


def _require_override_rights(
    principal: Principal, overrides: Iterable[ManualOverride | Keep | None]
) -> None:
    """Setting or clearing an override is a price decision: people with ``quotes:override``."""
    if any(override is not KEEP and override is not None for override in overrides):
        principal.require(Permission.QUOTES_OVERRIDE)


async def create_quote(
    principal: Principal, new: NewQuote, *, unit_of_work: UnitOfWorkFactory, clock: Clock
) -> Quote:
    """A draft numbered ``{prefix}-{year}-{sequence}`` (ADR-0021), its lines priced now."""
    principal.require(Permission.QUOTES_MANAGE)
    _require_override_rights(principal, (line.override for line in new.lines))
    now = clock()
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        tenant = await tenant_of(uow, principal.tenant_id)
        context = await pricing_context(
            uow, tenant, new.customer_id, {line.product_id for line in new.lines}, at=now
        )
        today = now.astimezone(UTC).date()
        # A quote refused below rolls the unit of work back, and the number with it (ADR-0021).
        sequence = await uow.quotes.allocate_number(today.year)
        quote = Quote.draft(
            number=quote_number(tenant.settings.quote_prefix, today.year, sequence),
            valid_until=new.valid_until
            or today + timedelta(days=tenant.settings.quote_validity_days),
            by=Actor.of(principal),
            context=context,
            notes=new.notes,
            lines=new.lines,
        )
        await uow.quotes.add(quote)
        changes = created(quote_fields(quote))
        await record(uow, principal, AuditAction.QUOTE_CREATED, quote.id, changes, now=now)
        await uow.commit()
    return quote


async def get_quote(
    principal: Principal, quote_id: UUID, *, unit_of_work: UnitOfWorkFactory
) -> Quote:
    principal.require(Permission.QUOTES_READ)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        quote = await uow.quotes.get(quote_id)
    if quote is None:
        raise NotFoundError("no such quote")
    return quote


async def _quote_at(uow: UnitOfWork, quote_id: UUID, expected_version: int) -> Quote:
    """The quote, if it is still at the version the caller read (ADR-0012)."""
    quote = await uow.quotes.get(quote_id)
    if quote is None:
        raise NotFoundError("no such quote")
    if quote.version != expected_version:
        raise StaleVersionError("the quote was changed by someone else; reload it")
    return quote


async def _repricing(
    uow: UnitOfWork, principal: Principal, quote: Quote, *, extra: UUID | None, now: datetime
) -> PricingContext:
    """What pricing every line of ``quote`` (and an ``extra`` product) needs now (ADR-0019)."""
    tenant = await tenant_of(uow, principal.tenant_id)
    products = {line.product_id for line in quote.lines} | ({extra} if extra else set())
    return await pricing_context(uow, tenant, quote.customer_id, products, at=now)


def _position(quote: QuoteSummary, sort: QuoteSort) -> Keyset:
    value = quote.valid_until.isoformat() if sort is QuoteSort.VALID_UNTIL else None
    return Keyset(quote.id, value)


async def list_quotes(
    principal: Principal,
    query: QuoteQuery,
    *,
    after: Keyset | None,
    limit: int,
    unit_of_work: UnitOfWorkFactory,
) -> Page[QuoteSummary]:
    principal.require(Permission.QUOTES_READ)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        quotes = await uow.quotes.page(query, after=after, limit=limit + 1)
    return page_of(quotes, limit, lambda quote: _position(quote, query.sort))


@dataclass(frozen=True, slots=True)
class QuoteTerms:
    """``valid_until`` left as ``None`` keeps its value; notes keep theirs with ``KEEP``."""

    valid_until: date | None = None
    notes: str | Keep | None = KEEP


async def change_quote_terms(
    principal: Principal,
    quote_id: UUID,
    terms: QuoteTerms,
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> Quote:
    """A draft's validity and notes; they do not change prices (ADR-0019)."""
    principal.require(Permission.QUOTES_MANAGE)
    now = clock()
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        quote = await _quote_at(uow, quote_id, expected_version)
        before = quote_fields(quote)
        quote.change_terms(valid_until=terms.valid_until, notes=terms.notes, now=now)
        await uow.quotes.save(quote)
        edits = changed(before, quote_fields(quote))
        await record(uow, principal, AuditAction.QUOTE_UPDATED, quote.id, edits, now=now)
        await uow.commit()
    return quote


async def add_quote_line(
    principal: Principal,
    quote_id: UUID,
    change: LineChange,
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> Quote:
    """Add a line to a draft; every line is priced again (ADR-0019)."""
    principal.require(Permission.QUOTES_MANAGE)
    _require_override_rights(principal, [change.override])
    now = clock()
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        quote = await _quote_at(uow, quote_id, expected_version)
        context = await _repricing(uow, principal, quote, extra=change.product_id, now=now)
        before = quote_fields(quote)
        line = quote.add_line(change, by=Actor.of(principal), context=context)
        await uow.quotes.save(quote)
        changes = {**created(quote_line_fields(line)), **changed(before, quote_fields(quote))}
        await record(uow, principal, AuditAction.QUOTE_LINE_ADDED, quote.id, changes, now=now)
        await uow.commit()
    return quote


@dataclass(frozen=True, slots=True)
class LineEdit:
    """``quantity`` left as ``None`` keeps its value; the override keeps it with ``KEEP``, and
    ``None`` removes it."""

    quantity: Decimal | None = None
    override: ManualOverride | Keep | None = KEEP


async def change_quote_line(
    principal: Principal,
    quote_id: UUID,
    line_id: UUID,
    edit: LineEdit,
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> Quote:
    principal.require(Permission.QUOTES_MANAGE)
    if edit.override is not KEEP:
        principal.require(Permission.QUOTES_OVERRIDE)  # clearing one is a price decision too
    now = clock()
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        quote = await _quote_at(uow, quote_id, expected_version)
        context = await _repricing(uow, principal, quote, extra=None, now=now)
        before, line_before = quote_fields(quote), quote_line_fields(quote.line(line_id))
        line = quote.change_line(
            line_id,
            quantity=edit.quantity,
            override=edit.override,
            by=Actor.of(principal),
            context=context,
        )
        await uow.quotes.save(quote)
        changes = {
            "line_id": (str(line_id), str(line_id)),
            **changed(line_before, quote_line_fields(line)),
            **changed(before, quote_fields(quote)),
        }
        await record(uow, principal, AuditAction.QUOTE_LINE_CHANGED, quote.id, changes, now=now)
        await uow.commit()
    return quote


async def remove_quote_line(
    principal: Principal,
    quote_id: UUID,
    line_id: UUID,
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> Quote:
    """Remove a line from a draft; the rest are priced again."""
    principal.require(Permission.QUOTES_MANAGE)
    now = clock()
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        quote = await _quote_at(uow, quote_id, expected_version)
        context = await _repricing(uow, principal, quote, extra=None, now=now)
        before = quote_fields(quote)
        line = quote.remove_line(line_id, context=context)
        await uow.quotes.save(quote)
        changes = {**removed(quote_line_fields(line)), **changed(before, quote_fields(quote))}
        await record(uow, principal, AuditAction.QUOTE_LINE_REMOVED, quote.id, changes, now=now)
        await uow.commit()
    return quote


async def _transition(
    principal: Principal,
    quote_id: UUID,
    permission: Permission,
    action: AuditAction,
    move: Callable[[Quote, datetime], None],
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> Quote:
    """Load the quote at the caller's version, move it, save it and record the move."""
    principal.require(permission)
    now = clock()
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        quote = await _quote_at(uow, quote_id, expected_version)
        before = quote_fields(quote)
        move(quote, now)
        await uow.quotes.save(quote)
        await record(
            uow, principal, action, quote.id, changed(before, quote_fields(quote)), now=now
        )
        await uow.commit()
    return quote


async def submit_quote(
    principal: Principal,
    quote_id: UUID,
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> Quote:
    """Price one last time and freeze (ADR-0019); ask for approval when needed (ADR-0020)."""
    principal.require(Permission.QUOTES_MANAGE)
    now = clock()
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        quote = await _quote_at(uow, quote_id, expected_version)
        context = await _repricing(uow, principal, quote, extra=None, now=now)
        before = quote_fields(quote)
        quote.submit(by=Actor.of(principal), context=context)
        await uow.quotes.save(quote)
        changes = dict(changed(before, quote_fields(quote)))
        if (request := quote.pending_approval) is not None:
            reasons = " ".join(reason.value for reason in request.reasons)
            changes["approval_reasons"] = (None, reasons)
        await record(uow, principal, AuditAction.QUOTE_SUBMITTED, quote.id, changes, now=now)
        await uow.commit()
    return quote


async def recall_quote(
    principal: Principal,
    quote_id: UUID,
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> Quote:
    """Withdraw a pending approval request; the quote is a draft again (ADR-0005)."""
    return await _transition(
        principal,
        quote_id,
        Permission.QUOTES_MANAGE,
        AuditAction.QUOTE_RECALLED,
        lambda quote, now: quote.recall(now=now),
        expected_version=expected_version,
        unit_of_work=unit_of_work,
        clock=clock,
    )


async def send_quote(
    principal: Principal,
    quote_id: UUID,
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> Quote:
    """Mark an approved quote as sent to the customer (people only: ``quotes:send``)."""
    return await _transition(
        principal,
        quote_id,
        Permission.QUOTES_SEND,
        AuditAction.QUOTE_SENT,
        lambda quote, now: quote.send(now=now),
        expected_version=expected_version,
        unit_of_work=unit_of_work,
        clock=clock,
    )


async def accept_quote(
    principal: Principal,
    quote_id: UUID,
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> Quote:
    """Record the customer's acceptance of a sent quote, within its validity."""
    return await _transition(
        principal,
        quote_id,
        Permission.QUOTES_SEND,
        AuditAction.QUOTE_ACCEPTED,
        lambda quote, now: quote.accept(now=now),
        expected_version=expected_version,
        unit_of_work=unit_of_work,
        clock=clock,
    )


async def cancel_quote(
    principal: Principal,
    quote_id: UUID,
    reason: str,
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> Quote:
    """Close an open quote for good, with a reason; also how a customer's "no" is recorded."""
    return await _transition(
        principal,
        quote_id,
        Permission.QUOTES_MANAGE,
        AuditAction.QUOTE_CANCELLED,
        lambda quote, now: quote.cancel(reason=reason, now=now),
        expected_version=expected_version,
        unit_of_work=unit_of_work,
        clock=clock,
    )


async def revise_quote(
    principal: Principal,
    quote_id: UUID,
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> Quote:
    """Supersede the quote with its next revision, a draft priced now (decision D-07)."""
    principal.require(Permission.QUOTES_MANAGE)
    now = clock()
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        quote = await _quote_at(uow, quote_id, expected_version)
        context = await _repricing(uow, principal, quote, extra=None, now=now)
        before = quote_fields(quote)
        successor = quote.revise(by=Actor.of(principal), context=context)
        # The old revision first: its version decides between concurrent revisions (ADR-0012).
        await uow.quotes.save(quote)
        await uow.quotes.add(successor)
        superseded = changed(before, quote_fields(quote))
        await record(uow, principal, AuditAction.QUOTE_REVISED, quote.id, superseded, now=now)
        fields = created(quote_fields(successor))
        await record(uow, principal, AuditAction.QUOTE_CREATED, successor.id, fields, now=now)
        await uow.commit()
    return successor
