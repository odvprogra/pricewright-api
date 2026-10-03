"""The real clock (the ``Clock`` port)."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)
