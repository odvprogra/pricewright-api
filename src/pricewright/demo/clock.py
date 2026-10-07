"""The time the demo's people act at (ADR-0024)."""

from datetime import datetime, timedelta

STEP = timedelta(seconds=10)
"""How long each master record takes to enter, so audit events keep their order in time."""


class DemoClock:
    """A clock the seed moves: it only goes forward, so audit events stay in order."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta = STEP) -> None:
        self.now += delta

    def set(self, moment: datetime) -> None:
        if moment < self.now:
            raise ValueError(f"the demo clock cannot go back from {self.now} to {moment}")
        self.now = moment
