"""Cursor pagination over HTTP (handbook §8, ADR-0014): ``?limit=&cursor=`` in, ``next_cursor`` out.

A cursor is opaque to clients: base64url of the last row's position and a fingerprint of the query
that produced it (its filters and sort). A cursor sent with a different query is rejected, as Google
AIP-158 asks, instead of returning a page of some other list.
"""

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from typing import Annotated
from uuid import UUID

from fastapi import Query

from pricewright.application.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Keyset
from pricewright.domain.errors import RuleViolationError

_FINGERPRINT_LENGTH = 16  # hex digits: a check against mistakes, not a security boundary


class InvalidCursorError(RuleViolationError):
    code = "invalid_cursor"


def _fingerprint(query: Mapping[str, object] | None) -> str:
    """Stable across requests: the same filters and sort always give the same fingerprint."""
    present = {name: str(value) for name, value in (query or {}).items() if value is not None}
    canonical = json.dumps(present, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:_FINGERPRINT_LENGTH]


def encode_cursor(after: Keyset | None, query: Mapping[str, object] | None = None) -> str | None:
    """The cursor for the page after ``after``; ``query`` holds the request's filters and sort."""
    if after is None:
        return None
    payload: dict[str, str] = {"id": str(after.id), "query": _fingerprint(query)}
    if after.value is not None:
        payload["value"] = after.value
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None, query: Mapping[str, object] | None = None) -> Keyset | None:
    if cursor is None:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        value = payload.get("value")
        if not isinstance(value, str | None):
            raise TypeError(value)
        after = Keyset(id=UUID(payload["id"]), value=value)
        fingerprint = payload["query"]
    except (binascii.Error, ValueError, TypeError, KeyError, AttributeError) as error:
        raise InvalidCursorError("cursor is not one this API returned") from error
    if fingerprint != _fingerprint(query):
        raise InvalidCursorError(
            "cursor belongs to another query: send it with the filters and sort that returned it"
        )
    return after


Limit = Annotated[
    int, Query(ge=1, le=MAX_PAGE_SIZE, description=f"Page size, 1 to {MAX_PAGE_SIZE}.")
]
Cursor = Annotated[
    str | None,
    Query(
        description="`next_cursor` from the previous page, with the same filters and sort; "
        "omit it for the first page."
    ),
]
DEFAULT_LIMIT = DEFAULT_PAGE_SIZE
