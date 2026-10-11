import io
import json
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from pricewright import main as composition
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
