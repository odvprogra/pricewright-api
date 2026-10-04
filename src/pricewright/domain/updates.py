"""Partial updates: telling "leave this field as it is" apart from "clear it" (``None``)."""

from enum import Enum
from typing import Literal


class _Keep(Enum):
    KEEP = "keep"


KEEP = _Keep.KEEP
"""Leaves an optional field as it is, where ``None`` means "clear it"."""
type Keep = Literal[_Keep.KEEP]
