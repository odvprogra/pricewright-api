"""Keyset pagination: a page continues after the last id of the previous one (handbook §8).

Ids are UUIDv7, so ordering by id is ordering by creation, and "after this id" stays correct when
rows are added or removed between requests, unlike an offset.
"""

from dataclasses import dataclass
from uuid import UUID

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: list[T]
    next_after: UUID | None
    """Where the next page starts, or None on the last page."""
