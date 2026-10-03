"""Optimistic concurrency over HTTP (ADR-0012): versions travel as strong ETags, and updates must
carry the ETag they were based on in ``If-Match``.
"""

import re
from http import HTTPStatus

from starlette.exceptions import HTTPException

from pricewright.domain.errors import StaleVersionError

_STRONG_ETAG = re.compile(r'"(\d+)"')


def etag(version: int) -> str:
    return f'"{version}"'


def expected_version(if_match: str | None) -> int:
    """The version an update was based on.

    Missing, or ``*`` (which RFC 9110 would let match any version): 428 Precondition Required.
    A weak or unknown tag never matches the current version: 412 Precondition Failed.
    """
    if if_match is None or if_match.strip() == "*":
        raise HTTPException(
            HTTPStatus.PRECONDITION_REQUIRED,
            detail="send If-Match with the ETag of the version you are changing",
        )
    match = _STRONG_ETAG.fullmatch(if_match.strip())
    if match is None:
        raise StaleVersionError("If-Match does not match the current ETag")
    return int(match.group(1))
