"""FastAPI application assembly: routers, middleware and error handlers.

No configuration is read here; the composition root passes everything in.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pricewright import __version__
from pricewright.api import auth, health, me, tenant
from pricewright.api.dependencies import Services
from pricewright.api.health import ReadinessCheck
from pricewright.api.middleware import RequestContextMiddleware
from pricewright.api.problems import register_problem_handlers

type ShutdownHook = Callable[[], Awaitable[None]]


def create_app(
    *,
    title: str,
    services: Services,
    readiness_checks: Mapping[str, ReadinessCheck] | None = None,
    on_shutdown: Sequence[ShutdownHook] = (),
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        for hook in on_shutdown:
            await hook()

    app = FastAPI(title=title, version=__version__, lifespan=lifespan)
    app.state.services = services
    app.state.readiness_checks = dict(readiness_checks or {})
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(me.router)
    app.include_router(tenant.router)
    register_problem_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    return app
