import io
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from pricewright import main as composition
from pricewright.domain.events import Event, EventType
from pricewright.infrastructure.logging import configure_logging, current_request_id
from pricewright.settings import Settings


def test_main_starts_uvicorn_with_settings_and_structured_logging(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("PORT", "8123")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda _app, **kwargs: calls.append(kwargs))

    composition.main()

    (kwargs,) = calls
    assert kwargs["port"] == 8123
    assert kwargs["log_config"] is None
    line = json.loads(capsys.readouterr().err.splitlines()[-1])
    assert line["event"] == "service.started"
    assert line["service"] == "pricewright-api"
    assert line["environment"] == "test"


def test_write_openapi_writes_the_application_spec(tmp_path: Path) -> None:
    spec_path = tmp_path / "openapi.json"

    composition.write_openapi(spec_path)

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    assert "/health/ready" in spec["paths"]


def test_build_app_checks_the_database_for_readiness() -> None:
    app = composition.build_app(Settings(_env_file=None))

    assert set(app.state.readiness_checks) == {"database"}


async def test_a_job_run_carries_a_request_id_of_its_own_and_logs_its_count() -> None:
    stream = io.StringIO()
    configure_logging("INFO", json=True, stream=stream)
    seen: list[str | None] = []

    async def expire() -> int:
        seen.append(current_request_id())
        return 3

    await composition.job("offers.expired", expire)()
    await composition.job("offers.expired", expire)()

    first, second = (json.loads(line) for line in stream.getvalue().splitlines())
    assert (first["event"], first["count"]) == ("offers.expired", 3)
    assert [first["request_id"], second["request_id"]] == seen
    assert seen[0] is not None
    assert seen[0] != seen[1]
    assert current_request_id() is None


async def test_a_job_run_that_changed_nothing_logs_nothing() -> None:
    stream = io.StringIO()
    configure_logging("INFO", json=True, stream=stream)

    async def relay() -> int:
        return 0

    await composition.job("events.relayed", relay)()

    assert stream.getvalue() == ""


def _approved(request_id: str | None) -> Event:
    return Event(
        id=uuid.uuid7(),
        tenant_id=uuid.uuid7(),
        type=EventType.QUOTE_APPROVED,
        aggregate_id=uuid.uuid7(),
        occurred_at=datetime(2026, 10, 15, 12, tzinfo=UTC),
        request_id=request_id,
    )


async def test_a_traced_consumer_logs_with_the_event_s_request_id() -> None:
    stream = io.StringIO()
    configure_logging("INFO", json=True, stream=stream)
    event = _approved("req-approve")
    seen: list[str | None] = []

    async def notify(_: Event) -> None:
        seen.append(current_request_id())

    await composition.traced(notify)(event)

    [line] = (json.loads(line) for line in stream.getvalue().splitlines())
    assert seen == ["req-approve"]
    assert (line["event"], line["request_id"]) == ("event.delivered", "req-approve")
    assert (line["event_id"], line["event_type"]) == (str(event.id), "quote.approved")
    assert current_request_id() is None


async def test_a_traced_consumer_logs_a_failure_and_lets_it_through() -> None:
    stream = io.StringIO()
    configure_logging("INFO", json=True, stream=stream)

    async def refuse(_: Event) -> None:
        raise ConnectionError("the mail server is down")

    with pytest.raises(ConnectionError):
        await composition.traced(refuse)(_approved(None))

    [line] = (json.loads(line) for line in stream.getvalue().splitlines())
    assert (line["event"], line["level"]) == ("event.failed", "error")
    assert line["exception"][0]["exc_type"] == "ConnectionError"
