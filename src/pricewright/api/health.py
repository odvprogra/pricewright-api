"""Health endpoints (HANDBOOK §8).

- ``/health/live``: the process is up. Never checks dependencies, so a database outage does not get
  healthy containers restarted.
- ``/health/ready``: the service can take traffic. Runs every readiness check registered at startup.
"""

from collections.abc import Awaitable, Callable, Mapping
from http import HTTPStatus

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

type ReadinessCheck = Callable[[], Awaitable[None]]
"""Raises if the dependency is not usable."""

router = APIRouter(prefix="/health", tags=["health"])
_log = structlog.get_logger(__name__)


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    checks: Mapping[str, ReadinessCheck] = request.app.state.readiness_checks
    results: dict[str, str] = {}
    for name, check in checks.items():
        try:
            await check()
        except Exception:
            _log.warning("readiness.check_failed", check=name, exc_info=True)
            results[name] = "unavailable"
        else:
            results[name] = "ok"

    healthy = all(result == "ok" for result in results.values())
    return JSONResponse(
        {"status": "ok" if healthy else "unavailable", "checks": results},
        status_code=HTTPStatus.OK if healthy else HTTPStatus.SERVICE_UNAVAILABLE,
    )
