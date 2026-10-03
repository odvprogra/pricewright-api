"""Keyset pagination: a page continues after the last row of the previous one (ADR-0014).

A row's position is its sort value plus its id, so "after this row" stays correct when rows are
added or removed between requests, unlike an offset. Ids are UUIDv7: ordering by id is ordering by
creation, and the id breaks ties between equal sort values.
"""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class Keyset:
    """A row's place in a sorted list: its id, and its sort value unless the sort is by id."""

    id: UUID
    value: str | None = None


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: list[T]
    next_after: Keyset | None
    """Where the next page starts, or None on the last page."""


def page_of[T](rows: list[T], limit: int, position: Callable[[T], Keyset]) -> Page[T]:
    """Cut ``rows``, fetched with ``limit + 1``, to a page; the extra row signals a next one."""
    items = rows[:limit]
    return Page(items=items, next_after=position(items[-1]) if len(rows) > limit else None)
