"""Quotes: reps, managers and integrations build them; the pricing engine prices them (ADR-0019).

Every use case binds the unit of work to the caller's tenant (ADR-0006), saves with the version the
caller read (ADR-0012) and records its audit event in the same unit of work (ADR-0013).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, timedelta
from uuid import UUID

from pricewright.application.audit import quote_fields, record
from pricewright.application.pagination import Keyset, Page, page_of
from pricewright.application.ports import (
    Clock,
    QuoteQuery,
    QuoteSort,
    QuoteSummary,
    UnitOfWorkFactory,
)
from pricewright.application.pricing import pricing_context, tenant_of
from pricewright.domain.actors import Actor
from pricewright.domain.audit import AuditAction, changed, created
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.quotes import LineChange, Quote, quote_number
from pricewright.domain.updates import KEEP, Keep


@dataclass(frozen=True, slots=True)
class NewQuote:
    customer_id: UUID
    valid_until: date | None = None
    """Defaults to today plus the tenant's quote validity."""
    notes: str | None = None
    lines: Sequence[LineChange] = field(default_factory=tuple)


async def create_quote(
    principal: Principal, new: NewQuote, *, unit_of_work: UnitOfWorkFactory, clock: Clock
) -> Quote:
    """A draft numbered ``{prefix}-{year}-{sequence}`` (ADR-0021), its lines priced now."""
    principal.require(Permission.QUOTES_MANAGE)
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
        quote = await uow.quotes.get(quote_id)
        if quote is None:
            raise NotFoundError("no such quote")
        if quote.version != expected_version:
            raise StaleVersionError("the quote was changed by someone else; reload it")
        before = quote_fields(quote)
        quote.change_terms(valid_until=terms.valid_until, notes=terms.notes, now=now)
        await uow.quotes.save(quote)
        edits = changed(before, quote_fields(quote))
        await record(uow, principal, AuditAction.QUOTE_UPDATED, quote.id, edits, now=now)
        await uow.commit()
    return quote
