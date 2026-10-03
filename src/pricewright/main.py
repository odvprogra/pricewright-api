"""Composition root: the only module that reads settings and wires adapters together."""

import asyncio
import json
import sys
from functools import partial
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from pricewright import __version__, cli
from pricewright.api.app import create_app
from pricewright.api.dependencies import Services
from pricewright.application.ports import UnitOfWork, UnitOfWorkFactory
from pricewright.infrastructure.database import create_engine, create_session_factory, ping
from pricewright.infrastructure.logging import configure_logging
from pricewright.infrastructure.passwords import Argon2PasswordHasher
from pricewright.infrastructure.tokens import JwtAccessTokens
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from pricewright.settings import LogFormat, Settings


def units_of_work(engine: AsyncEngine) -> UnitOfWorkFactory:
    sessions = create_session_factory(engine)

    def unit_of_work() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    return unit_of_work


def build_app(settings: Settings) -> FastAPI:
    """Wire the application for the given settings."""
    configure_logging(settings.log_level, json=settings.log_format is LogFormat.JSON)
    engine = create_engine(settings.database_url.get_secret_value())
    services = Services(
        unit_of_work=units_of_work(engine),
        hasher=Argon2PasswordHasher(),
        access_tokens=JwtAccessTokens(
            settings.jwt_secret.get_secret_value(), settings.access_token_ttl
        ),
    )
    return create_app(
        title=settings.service_name,
        services=services,
        readiness_checks={"database": partial(ping, engine)},
        on_shutdown=[engine.dispose],
    )


def app_factory() -> FastAPI:
    """Zero-argument factory for ``uvicorn --factory`` (``just dev``)."""
    return build_app(Settings())


def render_openapi(app: FastAPI) -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def write_openapi(path: Path = Path("openapi.json")) -> None:
    """Write the OpenAPI spec that is committed and checked for drift (``just openapi``)."""
    path.write_text(render_openapi(app_factory()), encoding="utf-8", newline="\n")


def main() -> None:
    """Start the HTTP server."""
    settings = Settings()
    app = build_app(settings)
    structlog.get_logger(__name__).info(
        "service.started",
        service=settings.service_name,
        version=__version__,
        environment=settings.environment,
        port=settings.port,
    )
    # log_config=None keeps uvicorn from replacing the structured logging configuration.
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None, access_log=False)


def admin() -> None:
    """Run an operator command (``pricewright-admin``) and exit with its status."""
    settings = Settings()
    configure_logging(settings.log_level, json=settings.log_format is LogFormat.JSON)

    async def run() -> int:
        engine = create_engine(settings.database_url.get_secret_value())
        try:
            return await cli.run(
                sys.argv[1:],
                unit_of_work=units_of_work(engine),
                hasher=Argon2PasswordHasher(),
                console=cli.Console(stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr),
            )
        finally:
            await engine.dispose()

    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
