import io
import json
import logging
from typing import Any

import structlog

from pricewright.infrastructure.logging import configure_logging


def _json_lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_json_logging_includes_level_utc_timestamp_and_bound_context() -> None:
    stream = io.StringIO()
    configure_logging("INFO", json=True, stream=stream)
    structlog.contextvars.bind_contextvars(request_id="req-123")

    structlog.get_logger("orders").info("order.created", order_id=42)

    (line,) = _json_lines(stream)
    assert line["event"] == "order.created"
    assert line["level"] == "info"
    assert line["logger"] == "orders"
    assert line["order_id"] == 42
    assert line["request_id"] == "req-123"
    assert line["timestamp"].endswith("Z")


def test_json_logging_renders_standard_library_records_in_same_format() -> None:
    stream = io.StringIO()
    configure_logging("INFO", json=True, stream=stream)

    logging.getLogger("uvicorn.error").warning("server started")

    (line,) = _json_lines(stream)
    assert line["event"] == "server started"
    assert line["level"] == "warning"
    assert line["logger"] == "uvicorn.error"


def test_json_logging_renders_exceptions_as_structured_data() -> None:
    stream = io.StringIO()
    configure_logging("INFO", json=True, stream=stream)

    try:
        raise ValueError("boom")
    except ValueError:
        structlog.get_logger("orders").exception("order.failed")

    (line,) = _json_lines(stream)
    assert line["exception"][0]["exc_type"] == "ValueError"


def test_logging_below_configured_level_is_dropped() -> None:
    stream = io.StringIO()
    configure_logging("WARNING", json=True, stream=stream)

    structlog.get_logger("orders").info("order.created")

    assert stream.getvalue() == ""


def test_console_logging_is_human_readable() -> None:
    stream = io.StringIO()
    configure_logging("INFO", json=False, stream=stream)

    structlog.get_logger("orders").info("order.created", order_id=42)

    output = stream.getvalue()
    assert "order.created" in output
    assert "order_id" in output
    assert not output.lstrip().startswith("{")
