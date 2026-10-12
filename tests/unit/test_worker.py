import asyncio
import io
import json
import signal
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
import structlog
from hypothesis import given
from hypothesis import strategies as st

from pricewright import main as composition
from pricewright.infrastructure.logging import configure_logging
from pricewright.infrastructure.worker import (
    JITTER,
    PeriodicTask,
    Worker,
    pause,
    stop_on_signals,
)
from pricewright.settings import Settings

NO_PAUSE = timedelta(0)


def _no_jitter() -> float:
    return 0.0


def _json_lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


@pytest.fixture
def _restore_signal_handlers() -> Iterator[None]:
    """``stop_on_signals`` replaces the process's handlers; later tests need the originals."""
    signals = (signal.SIGINT, signal.SIGTERM)
    previous = [signal.getsignal(signum) for signum in signals]
    yield
    for signum, handler in zip(signals, previous, strict=True):
        signal.signal(signum, handler)


async def test_worker_runs_a_task_at_start_and_again_after_each_pause() -> None:
    stop = asyncio.Event()
    runs = 0

    async def count() -> None:
        nonlocal runs
        runs += 1
        if runs == 3:
            stop.set()

    await Worker([PeriodicTask("count", NO_PAUSE, count)], draw=_no_jitter).run(stop)

    assert runs == 3


async def test_worker_logs_a_failed_run_and_runs_the_task_again() -> None:
    stream = io.StringIO()
    configure_logging("INFO", json=True, stream=stream)
    stop = asyncio.Event()
    attempts = 0

    async def flaky() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("the database is unreachable")
        stop.set()

    await Worker([PeriodicTask("flaky", NO_PAUSE, flaky)], draw=_no_jitter).run(stop)

    assert attempts == 2
    (failed,) = [line for line in _json_lines(stream) if line["event"] == "task.failed"]
    assert failed["task"] == "flaky"
    assert failed["level"] == "error"
    assert failed["exception"][0]["exc_type"] == "RuntimeError"


async def test_worker_names_the_task_on_every_line_its_runs_log() -> None:
    stream = io.StringIO()
    configure_logging("DEBUG", json=True, stream=stream)
    stop = asyncio.Event()

    async def report() -> None:
        structlog.get_logger("reports").info("report.sent")
        stop.set()

    await Worker([PeriodicTask("report", NO_PAUSE, report)], draw=_no_jitter).run(stop)

    lines = {line["event"]: line for line in _json_lines(stream)}
    assert lines["report.sent"]["task"] == "report"
    assert lines["task.completed"]["task"] == "report"
    assert "task" not in lines["worker.stopped"]


async def test_worker_lets_a_running_task_finish_before_it_stops() -> None:
    stop, started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    finished = False

    async def slow() -> None:
        nonlocal finished
        started.set()
        await release.wait()
        finished = True

    worker = Worker([PeriodicTask("slow", timedelta(hours=1), slow)])
    running = asyncio.create_task(worker.run(stop))
    await started.wait()
    stop.set()
    await asyncio.sleep(0)

    assert not running.done()
    release.set()
    await running
    assert finished


async def test_worker_without_tasks_waits_until_stopped() -> None:
    stream = io.StringIO()
    configure_logging("INFO", json=True, stream=stream)
    stop = asyncio.Event()
    running = asyncio.create_task(Worker([]).run(stop))
    await asyncio.sleep(0)

    assert not running.done()
    stop.set()
    await running
    events = [line["event"] for line in _json_lines(stream)]
    assert events == ["worker.started", "worker.stopped"]


@given(
    interval=st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=1)),
    draw=st.floats(min_value=0, max_value=1),
)
def test_pause_adds_at_most_the_jitter_to_the_interval(interval: timedelta, draw: float) -> None:
    seconds = pause(interval, draw)

    assert interval.total_seconds() <= seconds <= interval.total_seconds() * (1 + JITTER)


@pytest.mark.usefixtures("_restore_signal_handlers")
async def test_stop_on_signals_sets_the_stop_event_on_sigint() -> None:
    stop = asyncio.Event()
    stop_on_signals(stop)

    signal.raise_signal(signal.SIGINT)
    await asyncio.wait_for(stop.wait(), timeout=5)  # a guard against hanging, not a delay

    assert stop.is_set()


@pytest.mark.usefixtures("_restore_signal_handlers")
async def test_stop_on_signals_falls_back_to_process_handlers_without_loop_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows event loops have no signal handlers; every platform runs this path here."""

    def unsupported(*_args: object) -> None:
        raise NotImplementedError

    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", unsupported)
    stop = asyncio.Event()
    stop_on_signals(stop)

    signal.raise_signal(signal.SIGINT)
    await asyncio.wait_for(stop.wait(), timeout=5)  # a guard against hanging, not a delay

    assert stop.is_set()


async def test_run_worker_starts_the_service_tasks_and_stops_when_told() -> None:
    stream = io.StringIO()
    configure_logging("INFO", json=True, stream=stream)
    stop = asyncio.Event()
    stop.set()

    await composition.run_worker(Settings(_env_file=None), stop)

    started, stopped = _json_lines(stream)
    assert started["event"] == "worker.started"
    assert started["tasks"] == [
        "relay_events",
        "expire_offers",
        "purge_idempotency_keys",
        "purge_delivered_events",
    ]
    assert stopped["event"] == "worker.stopped"


def test_worker_entry_point_runs_the_worker_until_a_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    stops: list[asyncio.Event] = []
    runs: list[Settings] = []

    async def run_worker(settings: Settings, _stop: asyncio.Event) -> None:
        runs.append(settings)

    monkeypatch.setattr(composition, "stop_on_signals", stops.append)
    monkeypatch.setattr(composition, "run_worker", run_worker)

    composition.worker()

    (settings,) = runs
    assert settings.environment == "test"
    assert len(stops) == 1
