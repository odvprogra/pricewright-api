import io
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from starlette.types import Message, Receive, Scope, Send

from pricewright.api.middleware import RequestContextMiddleware
from pricewright.infrastructure.logging import configure_logging

UUID_V7 = 7


async def test_response_carries_a_generated_uuid7_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/live")

    assert uuid.UUID(response.headers["x-request-id"]).version == UUID_V7


async def test_valid_incoming_request_id_is_echoed(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/live", headers={"X-Request-ID": "upstream-abc.123"})

    assert response.headers["x-request-id"] == "upstream-abc.123"


@pytest.mark.parametrize("unsafe", ["has spaces", "x" * 129, "line\tbreak"])
async def test_unsafe_incoming_request_id_is_replaced(
    client: httpx.AsyncClient, unsafe: str
) -> None:
    response = await client.get("/health/live", headers={"X-Request-ID": unsafe})

    assert response.headers["x-request-id"] != unsafe
    assert uuid.UUID(response.headers["x-request-id"]).version == UUID_V7


async def test_each_request_logs_one_completion_line_with_its_request_id(
    client: httpx.AsyncClient,
) -> None:
    stream = io.StringIO()
    configure_logging("INFO", json=True, stream=stream)

    response = await client.get("/health/live")

    lines: list[dict[str, Any]] = [json.loads(line) for line in stream.getvalue().splitlines()]
    (completed,) = [line for line in lines if line["event"] == "request.completed"]
    assert completed["request_id"] == response.headers["x-request-id"]
    assert completed["method"] == "GET"
    assert completed["path"] == "/health/live"
    assert completed["status"] == response.status_code
    assert completed["duration_ms"] >= 0


async def test_non_http_events_pass_through_untouched() -> None:
    seen: list[Scope] = []

    async def inner(scope: Scope, _receive: Receive, _send: Send) -> None:
        seen.append(scope)

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(_message: Message) -> None:
        return None

    await RequestContextMiddleware(inner)({"type": "lifespan"}, receive, send)

    assert seen == [{"type": "lifespan"}]


async def test_failure_after_the_response_started_is_reraised(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"partial"
        raise RuntimeError("stream broke")

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        return StreamingResponse(chunks())

    with pytest.raises(RuntimeError, match="stream broke"):
        await client.get("/stream")
