"""The worker's jobs (ADR-0026): persist what the clock already decided, purge what has expired.

A job finds what is due across tenants (``uow.housekeeping``), then changes each record in a unit of
work bound to its tenant (ADR-0006). Jobs are idempotent and catch up by themselves: a run missed
while the worker was down is done by the next one, and two runs at once change each record once,
since saves compare versions (ADR-0012). Changes no one asked for are audited as the system's.
"""

from datetime import UTC, datetime

from pricewright.application.audit import quote_fields
from pricewright.application.ports import Clock, TenantRecordRef, UnitOfWorkFactory
from pricewright.domain.audit import AuditAction, AuditEvent, changed
from pricewright.domain.errors import StaleVersionError
from pricewright.domain.quote_lifecycle import QuoteAction

EXPIRY_BATCH = 100
"""Offers looked up per query; each is then expired in a unit of work of its own."""


async def expire_offers(
    *, unit_of_work: UnitOfWorkFactory, clock: Clock, batch: int = EXPIRY_BATCH
) -> int:
    """Persist ``expired`` for every offer in flight past its ``valid_until`` (ADR-0005), and
    withdraw its pending approval request. Return how many offers this run expired.

    Each offer is looked at once per run, in id order, so a run always ends.
    """
    now = clock()
    today = now.astimezone(UTC).date()
    expired = 0
    after = None
    while True:
        async with unit_of_work() as uow:
            due = await uow.housekeeping.offers_past_validity(today, after=after, limit=batch)
        for offer in due:
            expired += await _expire(offer, unit_of_work=unit_of_work, now=now)
        if len(due) < batch:
            return expired
        after = due[-1].id


async def _expire(offer: TenantRecordRef, *, unit_of_work: UnitOfWorkFactory, now: datetime) -> int:
    async with unit_of_work() as uow:
        uow.bind_tenant(offer.tenant_id)
        quote = await uow.quotes.get(offer.id)
        if quote is None or QuoteAction.EXPIRE not in quote.allowed_actions(now):
            return 0  # changed since it was found: revised, cancelled or expired by another run
        before = quote_fields(quote)
        quote.expire(now=now)
        try:
            await uow.quotes.save(quote)
        except StaleVersionError:
            return 0  # changed at the same moment; if it is still due, the next run expires it
        changes = changed(before, quote_fields(quote))
        await uow.audit_events.add(
            AuditEvent.by_system(
                offer.tenant_id, AuditAction.QUOTE_EXPIRED, quote.id, changes, now=now
            )
        )
        await uow.commit()
    return 1
