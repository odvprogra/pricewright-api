"""Cursor pagination over HTTP (handbook §8): ``?limit=&cursor=`` in, ``next_cursor`` out.

Cursors are opaque to clients (base64url of the last id), so the paging scheme can change without
breaking them.
"""

import base64
import binascii
from typing import Annotated
from uuid import UUID

from fastapi import Query

from pricewright.application.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from pricewright.domain.errors import RuleViolationError


class InvalidCursorError(RuleViolationError):
    code = "invalid_cursor"


def encode_cursor(after: UUID | None) -> str | None:
    if after is None:
        return None
    return base64.urlsafe_b64encode(after.bytes).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> UUID | None:
    if cursor is None:
        return None
    try:
        return UUID(bytes=base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (binascii.Error, ValueError) as error:
        raise InvalidCursorError("cursor is not one this API returned") from error


Limit = Annotated[
    int, Query(ge=1, le=MAX_PAGE_SIZE, description=f"Page size, 1 to {MAX_PAGE_SIZE}.")
]
Cursor = Annotated[
    str | None, Query(description="`next_cursor` from the previous page; omit for the first.")
]
DEFAULT_LIMIT = DEFAULT_PAGE_SIZE
