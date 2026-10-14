"""Quote documents: the PDF a customer receives, rendered on request from the frozen snapshot.

Nothing is stored (ADR-0027): a submitted quote never changes (ADR-0019), so rendering it again
gives the same document; drafts render as drafts. A document is for the customer, so it never
shows costs or margins, whoever asks for it (ADR-0017).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pricewright.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from pricewright.application.pricing import tenant_of
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.customers import Customer
from pricewright.domain.errors import NotFoundError
from pricewright.domain.quote_lifecycle import QuoteStatus, effective_status
from pricewright.domain.quotes import Quote
from pricewright.domain.tenants import Tenant


@dataclass(frozen=True, slots=True)
class QuoteDocument:
    """Everything a quote's PDF shows, as of ``now``."""

    tenant: Tenant
    quote: Quote
    customer: Customer
    prepared_by: str | None
    """The name of the person who created the quote; none for an integration."""
    status: QuoteStatus
    """What the quote is at ``now``: an offer past its date is expired, stored or not."""
    now: datetime

    @property
    def filename(self) -> str:
        return f"{self.quote.display_number}.pdf"


class QuoteDocuments(Protocol):
    """Renders quote documents (the WeasyPrint adapter)."""

    async def render(self, document: QuoteDocument) -> bytes: ...


@dataclass(frozen=True, slots=True)
class RenderedQuote:
    filename: str
    content: bytes


async def quote_document(uow: UnitOfWork, quote: Quote, *, now: datetime) -> QuoteDocument:
    """What a quote's document shows, read in a unit of work bound to the quote's tenant."""
    customer = await uow.customers.get(quote.customer_id)
    if customer is None:  # pragma: no cover - a quote's customer is archived, never deleted
        raise NotFoundError("the quote's customer no longer exists")
    creator = await uow.users.get(quote.created_by.id) if quote.created_by.is_person else None
    return QuoteDocument(
        tenant=await tenant_of(uow, quote.tenant_id),
        quote=quote,
        customer=customer,
        prepared_by=None if creator is None else creator.full_name,
        status=effective_status(quote.status, quote.valid_until, now),
        now=now,
    )


async def render_quote(
    principal: Principal,
    quote_id: UUID,
    *,
    unit_of_work: UnitOfWorkFactory,
    documents: QuoteDocuments,
    clock: Clock,
) -> RenderedQuote:
    """The quote's PDF as it stands now (``quotes:read``)."""
    principal.require(Permission.QUOTES_READ)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        quote = await uow.quotes.get(quote_id)
        if quote is None:
            raise NotFoundError("no such quote")
        document = await quote_document(uow, quote, now=clock())
    content = await documents.render(document)
    return RenderedQuote(document.filename, content)
