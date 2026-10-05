"""The SQLAlchemy quote repository (ADR-0011): a quote, its lines and its approval requests.

Statements are explicit (INSERT, compare-and-set UPDATE, DELETE), in the order the foreign keys
need, so nothing depends on how the ORM orders a flush across tables.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, delete, insert, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pricewright.application.pagination import Keyset
from pricewright.application.ports import (
    ApprovalSummary,
    QuoteQuery,
    QuoteSort,
    QuoteSummary,
)
from pricewright.domain.actors import Actor
from pricewright.domain.audit import ActorType
from pricewright.domain.catalog import UnitOfMeasure
from pricewright.domain.errors import StaleVersionError
from pricewright.domain.money import Money
from pricewright.domain.numbering import NumberSeries
from pricewright.domain.pricing import ApprovalReason
from pricewright.domain.quote_approvals import ApprovalRequest, ApprovalStatus
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.quotes import Quote, QuoteLine, QuoteTotals
from pricewright.infrastructure.line_snapshots import override_of, priced_line, snapshot_values
from pricewright.infrastructure.numbering import allocate_number
from pricewright.infrastructure.records import (
    ApprovalRequestRecord,
    QuoteLineRecord,
    QuoteRecord,
)
from pricewright.infrastructure.repositories import TenantScope


def _actor(kind: str, actor_id: UUID) -> Actor:
    return Actor(ActorType(kind), actor_id)


# Column values for SQL statements: ``Any`` at this boundary, since each column has its own type.


def _quote_values(quote: Quote) -> dict[str, Any]:
    """The columns a save may change."""
    totals, submitted_by = quote.totals, quote.submitted_by
    return {
        "status": quote.status.value,
        "valid_until": quote.valid_until,
        "notes": quote.notes,
        "status_changed_at": quote.status_changed_at,
        "submitted_by_type": None if submitted_by is None else submitted_by.type.value,
        "submitted_by_id": None if submitted_by is None else submitted_by.id,
        "submitted_at": quote.submitted_at,
        "cancel_reason": quote.cancel_reason,
        "superseded_by_id": quote.superseded_by_id,
        "order_id": quote.order_id,
        "list_subtotal": totals.list_subtotal.amount,
        "net_subtotal": totals.net_subtotal.amount,
        "tax_rate": totals.tax_rate,
        "tax": totals.tax.amount,
        "total": totals.total.amount,
        "approval_threshold": totals.approval_threshold,
        "priced_at": totals.priced_at,
    }


def _line_values(quote: Quote, line: QuoteLine, position: int) -> dict[str, Any]:
    return {
        "id": line.id,
        "tenant_id": quote.tenant_id,
        "quote_id": quote.id,
        "position": position,
        **snapshot_values(line, quote.currency),
        "added_by_type": line.added_by.type.value,
        "added_by_id": line.added_by.id,
    }


def _approval_values(quote: Quote, request: ApprovalRequest) -> dict[str, Any]:
    return {
        "id": request.id,
        "tenant_id": quote.tenant_id,
        "quote_id": quote.id,
        "requested_by_type": request.requested_by.type.value,
        "requested_by_id": request.requested_by.id,
        "requested_at": request.requested_at,
        "reasons": [reason.value for reason in request.reasons],
        "discount": request.discount,
        "approval_threshold": request.approval_threshold,
        "currency": quote.currency,
        "list_subtotal": request.list_subtotal.amount,
        "net_subtotal": request.net_subtotal.amount,
        "status": request.status.value,
        "decided_by": request.decided_by,
        "decided_at": request.decided_at,
        "comment": request.comment,
    }


def _to_line(record: QuoteLineRecord) -> QuoteLine:
    return QuoteLine(
        id=record.id,
        product_id=record.product_id,
        sku=record.sku,
        product_name=record.product_name,
        unit=UnitOfMeasure(record.unit),
        quantity=record.quantity,
        added_by=_actor(record.added_by_type, record.added_by_id),
        pricing=priced_line(record),
        override=override_of(record),
        override_by=record.override_by,
    )


def _to_approval(record: ApprovalRequestRecord) -> ApprovalRequest:
    currency = record.currency
    return ApprovalRequest(
        id=record.id,
        requested_by=_actor(record.requested_by_type, record.requested_by_id),
        requested_at=record.requested_at,
        reasons=tuple(ApprovalReason(reason) for reason in record.reasons),
        discount=record.discount,
        approval_threshold=record.approval_threshold,
        list_subtotal=Money(record.list_subtotal, currency),
        net_subtotal=Money(record.net_subtotal, currency),
        status=ApprovalStatus(record.status),
        decided_by=record.decided_by,
        decided_at=record.decided_at,
        comment=record.comment,
    )


def _to_quote(
    record: QuoteRecord,
    lines: Sequence[QuoteLineRecord],
    approvals: Sequence[ApprovalRequestRecord],
) -> Quote:
    currency = record.currency
    submitted_by = (
        None
        if record.submitted_by_type is None or record.submitted_by_id is None
        else _actor(record.submitted_by_type, record.submitted_by_id)
    )
    return Quote(
        id=record.id,
        tenant_id=record.tenant_id,
        number=record.number,
        revision=record.revision,
        customer_id=record.customer_id,
        currency=currency,
        valid_until=record.valid_until,
        created_by=_actor(record.created_by_type, record.created_by_id),
        created_at=record.created_at,
        totals=QuoteTotals(
            list_subtotal=Money(record.list_subtotal, currency),
            net_subtotal=Money(record.net_subtotal, currency),
            tax_rate=record.tax_rate,
            tax=Money(record.tax, currency),
            total=Money(record.total, currency),
            approval_threshold=record.approval_threshold,
            priced_at=record.priced_at,
        ),
        lines=[_to_line(line) for line in lines],
        notes=record.notes,
        status=QuoteStatus(record.status),
        status_changed_at=record.status_changed_at,
        approvals=[_to_approval(approval) for approval in approvals],
        submitted_by=submitted_by,
        submitted_at=record.submitted_at,
        cancel_reason=record.cancel_reason,
        supersedes_id=record.supersedes_id,
        superseded_by_id=record.superseded_by_id,
        order_id=record.order_id,
        version=record.version,
    )


def _to_summary(record: QuoteRecord) -> QuoteSummary:
    return QuoteSummary(
        id=record.id,
        number=record.number,
        revision=record.revision,
        customer_id=record.customer_id,
        status=QuoteStatus(record.status),
        valid_until=record.valid_until,
        net_subtotal=Money(record.net_subtotal, record.currency),
        total=Money(record.total, record.currency),
        created_by=_actor(record.created_by_type, record.created_by_id),
        created_at=record.created_at,
        status_changed_at=record.status_changed_at,
        version=record.version,
    )


class SqlAlchemyQuoteRepository:
    def __init__(self, session: AsyncSession, scope: TenantScope) -> None:
        self._session = session
        self._scope = scope

    def _require_own(self, quote: Quote) -> None:
        if quote.tenant_id != self._scope.tenant_id:
            raise RuntimeError("only a quote of the unit of work's tenant can be stored")

    async def allocate_number(self, year: int) -> int:
        return await allocate_number(self._session, self._scope.tenant_id, NumberSeries.QUOTE, year)

    async def add(self, quote: Quote) -> None:
        self._require_own(quote)
        created_by = quote.created_by
        await self._session.execute(
            insert(QuoteRecord).values(
                id=quote.id,
                tenant_id=quote.tenant_id,
                number=quote.number,
                revision=quote.revision,
                customer_id=quote.customer_id,
                currency=quote.currency,
                created_by_type=created_by.type.value,
                created_by_id=created_by.id,
                created_at=quote.created_at,
                supersedes_id=quote.supersedes_id,
                version=quote.version,
                **_quote_values(quote),
            )
        )
        await self._insert_children(quote)

    async def get(self, quote_id: UUID) -> Quote | None:
        tenant_id = self._scope.tenant_id
        record = await self._session.scalar(
            select(QuoteRecord).where(
                QuoteRecord.tenant_id == tenant_id, QuoteRecord.id == quote_id
            )
        )
        if record is None:
            return None
        lines = await self._session.scalars(
            select(QuoteLineRecord)
            .where(QuoteLineRecord.tenant_id == tenant_id, QuoteLineRecord.quote_id == quote_id)
            .order_by(QuoteLineRecord.position)
        )
        approvals = await self._session.scalars(
            select(ApprovalRequestRecord)
            .where(
                ApprovalRequestRecord.tenant_id == tenant_id,
                ApprovalRequestRecord.quote_id == quote_id,
            )
            .order_by(ApprovalRequestRecord.id)
        )
        return _to_quote(record, list(lines), list(approvals))

    async def page(
        self, query: QuoteQuery, *, after: Keyset | None, limit: int
    ) -> list[QuoteSummary]:
        conditions: list[ColumnElement[bool]] = [QuoteRecord.tenant_id == self._scope.tenant_id]
        if query.status is not None:
            conditions.append(QuoteRecord.status == query.status.value)
        if query.customer_id is not None:
            conditions.append(QuoteRecord.customer_id == query.customer_id)
        if query.number is not None:
            conditions.append(QuoteRecord.number == query.number)
        if query.created_by is not None:
            conditions.append(QuoteRecord.created_by_id == query.created_by)
        # Keyset on (sort value, id), or on the id alone (ADR-0014): one index range scan.
        by_date = query.sort is QuoteSort.VALID_UNTIL
        key = (QuoteRecord.valid_until, QuoteRecord.id) if by_date else (QuoteRecord.id,)
        if after is not None:
            position = (date.fromisoformat(after.value or ""), after.id) if by_date else (after.id,)
            rows, start = tuple_(*key), tuple_(*position)
            conditions.append(rows < start if query.descending else rows > start)
        order = [part.desc() if query.descending else part.asc() for part in key]
        records = await self._session.scalars(
            select(QuoteRecord).where(*conditions).order_by(*order).limit(limit)
        )
        return [_to_summary(record) for record in records]

    async def approval_page(
        self, status: ApprovalStatus, *, today: date, after: UUID | None, limit: int
    ) -> list[ApprovalSummary]:
        tenant_id = self._scope.tenant_id
        request, quote = ApprovalRequestRecord, QuoteRecord
        conditions: list[ColumnElement[bool]] = [
            request.tenant_id == tenant_id,
            request.status == status.value,
        ]
        if status is ApprovalStatus.PENDING:
            conditions.append(quote.valid_until >= today)  # an expired offer is only revised
        if after is not None:
            conditions.append(request.id > after)
        rows = await self._session.execute(
            select(request, quote)
            .join(quote, (quote.tenant_id == request.tenant_id) & (quote.id == request.quote_id))
            .where(*conditions)
            .order_by(request.id)
            .limit(limit)
        )
        return [ApprovalSummary(_to_approval(r), _to_summary(q)) for r, q in rows]

    async def save(self, quote: Quote) -> None:
        self._require_own(quote)
        tenant_id = self._scope.tenant_id
        # Compare-and-set first: of two concurrent saves, the second matches no row (ADR-0012).
        new_version = await self._session.scalar(
            update(QuoteRecord)
            .where(
                QuoteRecord.tenant_id == tenant_id,
                QuoteRecord.id == quote.id,
                QuoteRecord.version == quote.version,
            )
            .values(**_quote_values(quote), version=QuoteRecord.version + 1)
            .returning(QuoteRecord.version)
        )
        if new_version is None:
            raise StaleVersionError("the quote was changed by someone else; reload it")
        await self._session.execute(
            delete(QuoteLineRecord).where(
                QuoteLineRecord.tenant_id == tenant_id, QuoteLineRecord.quote_id == quote.id
            )
        )
        await self._insert_children(quote)
        quote.version = new_version

    async def _insert_children(self, quote: Quote) -> None:
        """The quote's lines, then its approval requests (new or updated)."""
        if quote.lines:
            await self._session.execute(
                insert(QuoteLineRecord),
                [_line_values(quote, line, position) for position, line in enumerate(quote.lines)],
            )
        for request in quote.approvals:
            values = _approval_values(quote, request)
            await self._session.execute(
                pg_insert(ApprovalRequestRecord)
                .values(values)
                .on_conflict_do_update(
                    index_elements=[ApprovalRequestRecord.id],
                    set_={
                        key: values[key]
                        for key in ("status", "decided_by", "decided_at", "comment")
                    },
                )
            )
