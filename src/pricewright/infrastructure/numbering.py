"""Document number counters (ADR-0021): one row per tenant, series and year."""

from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pricewright.domain.numbering import NumberSeries
from pricewright.infrastructure.records import DocumentNumberCounterRecord


async def allocate_number(
    session: AsyncSession, tenant_id: UUID, series: NumberSeries, year: int
) -> int:
    """The next number of the series. The upsert locks the counter row until commit: concurrent
    allocations in the same tenant, series and year wait, and a rollback takes the number back."""
    counter = DocumentNumberCounterRecord
    allocated = await session.scalar(
        pg_insert(counter)
        .values(tenant_id=tenant_id, series=series.value, year=year, last_value=1)
        .on_conflict_do_update(
            index_elements=[counter.tenant_id, counter.series, counter.year],
            set_={"last_value": counter.last_value + 1},
        )
        .returning(counter.last_value)
    )
    if allocated is None:  # pragma: no cover - an upsert with RETURNING always returns a row
        raise RuntimeError("the document number counter returned nothing")
    return int(allocated)
