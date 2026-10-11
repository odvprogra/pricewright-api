"""The background worker: periodic tasks in their own process, from the same image as the service.

Each task runs once when the worker starts, then again every ``interval`` plus up to 10% jitter, so
replicas drift apart instead of running in lockstep. A task that fails is logged and runs again at
its next turn: one failure never stops the worker. Stopping (SIGINT, SIGTERM) lets every running
task finish its current run, then the worker exits.
"""

import asyncio
import random
import signal
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta

import structlog

JITTER = 0.1
"""Up to 10% of the interval is added to each pause."""


def _logger() -> structlog.stdlib.BoundLogger:
    # Looked up on each use: the logging configuration may change after import (tests do).
    return structlog.stdlib.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PeriodicTask:
    name: str
    interval: timedelta
    run: Callable[[], Awaitable[None]]


def pause(interval: timedelta, draw: float) -> float:
    """Seconds to wait before the next run: the interval plus ``draw`` (0 to 1) of the jitter."""
    return interval.total_seconds() * (1 + JITTER * draw)


class Worker:
    def __init__(
        self, tasks: Sequence[PeriodicTask], *, draw: Callable[[], float] = random.random
    ) -> None:
        self._tasks = tuple(tasks)
        self._draw = draw

    async def run(self, stop: asyncio.Event) -> None:
        """Run every task until ``stop`` is set, then wait for the runs in progress."""
        _logger().info("worker.started", tasks=[task.name for task in self._tasks])
        async with asyncio.TaskGroup() as group:
            for task in self._tasks:
                group.create_task(self._repeat(task, stop), name=task.name)
            await stop.wait()
        _logger().info("worker.stopped")

    async def _repeat(self, task: PeriodicTask, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self._run_once(task)
            try:
                await asyncio.wait_for(stop.wait(), timeout=pause(task.interval, self._draw()))
            except TimeoutError:
                continue

    async def _run_once(self, task: PeriodicTask) -> None:
        # Every line the run logs names its task (each asyncio task has its own context).
        with structlog.contextvars.bound_contextvars(task=task.name):
            started = time.perf_counter()
            try:
                await task.run()
            except Exception:
                # Logged, never raised: the task runs again at its next turn.
                _logger().exception("task.failed")
                return
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            _logger().debug("task.completed", duration_ms=duration_ms)


def stop_on_signals(stop: asyncio.Event) -> None:
    """Set ``stop`` on SIGINT or SIGTERM (``docker stop`` sends SIGTERM)."""
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:  # Windows event loops have no signal handlers
            signal.signal(signum, lambda _signum, _frame: loop.call_soon_threadsafe(stop.set))
