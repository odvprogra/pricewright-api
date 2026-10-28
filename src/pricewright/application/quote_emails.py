"""Quote emails (ADR-0028): the outbox's consumers for approved, rejected and sent quotes.

Events are thin (ADR-0008), so each consumer reads what it needs when it runs, in a unit of work
bound to the event's tenant: the quote, its people and its customer as they are now. Each sends at
most one email, identified by its event, so a second delivery of the event is recognizable as the
same message.
"""

from uuid import UUID

from pricewright.application.documents import QuoteDocument, QuoteDocuments, quote_document
from pricewright.application.notifications import Attachment, Email, Mailer
from pricewright.application.outbox import Consumer
from pricewright.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from pricewright.domain.actors import Actor
from pricewright.domain.events import Event, EventType
from pricewright.domain.quote_approvals import ApprovalRequest, ApprovalStatus
from pricewright.domain.quotes import Quote
from pricewright.domain.users import User

PDF = "application/pdf"


async def _person(uow: UnitOfWork, actor: Actor | None) -> User | None:
    """The user behind an actor; an integration has no mailbox."""
    if actor is None or not actor.is_person:
        return None
    return await uow.users.get(actor.id)


def _decision(quote: Quote, status: ApprovalStatus) -> ApprovalRequest | None:
    return next((r for r in reversed(quote.approvals) if r.status is status), None)


class QuoteEmails:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        documents: QuoteDocuments,
        mailer: Mailer,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._documents = documents
        self._mailer = mailer
        self._clock = clock

    def consumers(self) -> dict[EventType, Consumer]:
        return {
            EventType.QUOTE_APPROVED: self.approved,
            EventType.QUOTE_REJECTED: self.rejected,
            EventType.QUOTE_SENT: self.sent,
        }

    async def approved(self, event: Event) -> None:
        """The rep who submitted the quote gets the approval and the PDF to send."""
        await self._decided(event, ApprovalStatus.APPROVED, "quote_approved", "approved")

    async def rejected(self, event: Event) -> None:
        """The rep who submitted the quote gets the manager's comment on what to change."""
        await self._decided(event, ApprovalStatus.REJECTED, "quote_rejected", "needs changes")

    async def sent(self, event: Event) -> None:
        """The customer gets the quote's PDF, with replies going to the rep who sent it. Without
        an address on the customer, the rep delivers it: nothing is sent."""
        async with self._unit_of_work() as uow:
            uow.bind_tenant(event.tenant_id)
            document = await self._document(uow, event)
            # Only people send quotes (ADR-0020): the event's actor is one of the tenant's users.
            rep = await uow.users.get(UUID(event.data["actor_id"]))
        customer = document.customer
        if customer.email is None:
            return
        quote = document.quote
        await self._mailer.send(
            Email(
                message_id=str(event.id),
                to=customer.email,
                subject=f"Quotation {quote.display_number} from {document.tenant.name}",
                template="quote_sent",
                sender_name=document.tenant.name,
                values=self._values(document, rep=rep),
                reply_to=None if rep is None else rep.email,
                attachments=(await self._pdf(document),),
            )
        )

    async def _decided(
        self, event: Event, status: ApprovalStatus, template: str, verdict: str
    ) -> None:
        async with self._unit_of_work() as uow:
            uow.bind_tenant(event.tenant_id)
            document = await self._document(uow, event)
            quote = document.quote
            rep = await _person(uow, quote.submitted_by)
            request = _decision(quote, status)
            decider_id = None if request is None else request.decided_by
            decider = None if decider_id is None else await uow.users.get(decider_id)
        if rep is None:  # an integration submitted it: no one to tell
            return
        values = self._values(document, rep=rep)
        values["decider"] = "A manager" if decider is None else decider.full_name
        values["comment"] = None if request is None else request.comment
        approved = status is ApprovalStatus.APPROVED
        await self._mailer.send(
            Email(
                message_id=str(event.id),
                to=rep.email,
                subject=f"Quote {quote.display_number} {verdict}",
                template=template,
                sender_name=document.tenant.name,
                values=values,
                attachments=(await self._pdf(document),) if approved else (),
            )
        )

    async def _document(self, uow: UnitOfWork, event: Event) -> QuoteDocument:
        quote = await uow.quotes.get(event.aggregate_id)
        if quote is None:  # pragma: no cover - quotes are never deleted (ADR-0005)
            raise LookupError(f"the quote of event {event.id} no longer exists")
        return await quote_document(uow, quote, now=self._clock())

    async def _pdf(self, document: QuoteDocument) -> Attachment:
        return Attachment(document.filename, await self._documents.render(document), PDF)

    @staticmethod
    def _values(document: QuoteDocument, *, rep: User | None) -> dict[str, object]:
        quote = document.quote
        return {
            "tenant": document.tenant.name,
            "number": quote.display_number,
            "customer": document.customer.name,
            "total": quote.totals.total,
            "valid_until": quote.valid_until,
            "rep": None if rep is None else rep.full_name,
            "rep_email": None if rep is None else rep.email,
        }
