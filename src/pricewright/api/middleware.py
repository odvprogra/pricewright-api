"""Request context: a request ID on every log line and response, one log line per request.

A pure ASGI middleware (not ``BaseHTTPMiddleware``) so context variables propagate correctly.
It is the outermost application layer, so it also turns unhandled exceptions into a 500 Problem
Details response that still carries ``X-Request-ID``.
"""

import re
import time
import uuid
from http import HTTPStatus

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pricewright.api.problems import problem_response

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller's ID only if it is safe to log and echo back.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

_log = structlog.get_logger(__name__)


def _request_id(scope: Scope) -> str:
    incoming = Headers(scope=scope).get(REQUEST_ID_HEADER)
    if incoming and _VALID_REQUEST_ID.fullmatch(incoming):
        return incoming
    return str(uuid.uuid7())


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = _request_id(scope)
        started = time.perf_counter()
        status_code = HTTPStatus.INTERNAL_SERVER_ERROR.value
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                await self._app(scope, receive, send_with_request_id)
            except Exception:
                _log.exception("request.failed")
                if response_started:
                    raise
                response = problem_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    code="internal_error",
                    instance=scope["path"],
                    detail="An unexpected error occurred. Quote the request ID when reporting it.",
                )
                await response(scope, receive, send_with_request_id)
            finally:
                _log.info(
                    "request.completed",
                    method=scope["method"],
                    path=scope["path"],
                    status=status_code,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )
