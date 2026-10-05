"""Document numbers people read out: the tenant's prefix, the year and a counter (ADR-0021).

Quotes and orders are numbered alike, each in its own series with its own prefix, as SAP assigns a
number range per document type and Dynamics 365 a prefix per record type.
"""

from enum import StrEnum

SEQUENCE_DIGITS = 6


class NumberSeries(StrEnum):
    """A tenant counts each series apart, per year."""

    QUOTE = "quote"
    ORDER = "order"


def document_number(prefix: str, year: int, sequence: int) -> str:
    """``NF-2026-000123``: the prefix, the year and the tenant's count in that year and series."""
    return f"{prefix}-{year}-{sequence:0{SEQUENCE_DIGITS}d}"
