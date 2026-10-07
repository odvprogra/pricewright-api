"""Seeded choices built only on ``random()`` (ADR-0024).

``random()`` is the one method whose sequence Python keeps across versions for a seed given to the
compatible seeder (``version=2``); ``choice``, ``shuffle`` and ``randrange`` may change. So the
same seed gives the same demo data on every Python.
"""

import random
from collections.abc import Sequence


def generator(seed: int, purpose: str) -> random.Random:
    """A generator of its own per purpose, so drawing more for one never shifts another."""
    rng = random.Random()
    rng.seed(f"{seed}/{purpose}", version=2)
    return rng


def pick[T](rng: random.Random, options: Sequence[T]) -> T:
    return options[int(rng.random() * len(options))]


def between(rng: random.Random, low: int, high: int) -> int:
    """A whole number from ``low`` to ``high``, both included."""
    return low + int(rng.random() * (high - low + 1))


def chance(rng: random.Random, probability: float) -> bool:
    return rng.random() < probability


def shuffled[T](rng: random.Random, items: Sequence[T]) -> list[T]:
    """A Fisher-Yates shuffle of a copy."""
    result = list(items)
    for index in range(len(result) - 1, 0, -1):
        other = int(rng.random() * (index + 1))
        result[index], result[other] = result[other], result[index]
    return result
