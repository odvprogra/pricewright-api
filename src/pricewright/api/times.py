"""Times in requests: with their offset, kept in UTC (handbook §5)."""

from datetime import UTC
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime

UtcDatetime = Annotated[AwareDatetime, AfterValidator(lambda moment: moment.astimezone(UTC))]
"""A time sent with its offset (``-04:00``, ``Z``), converted to UTC; a naive time is a 422."""
