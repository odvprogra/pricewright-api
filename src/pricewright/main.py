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
from pricewright.infrastructure.clock import utc_now
from pricewright.infrastructure.database import create_engine, create_session_factory, ping
from pricewright.infrastructure.logging import configure_logging
from pricewright.infrastructure.passwords import Argon2PasswordHasher
from pricewright.infrastructure.tokens import JwtAccessTokens
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from pricewright.settings import Environment, LogFormat, Settings

DEMO_ENVIRONMENTS = frozenset({Environment.LOCAL, Environment.TEST})
"""Where ``pricewright-admin seed`` may load demo users with their published passphrase."""


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
        clock=utc_now,
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


def _whole_numbers_as_integers(value: object) -> object:
    """FastAPI types numeric limits as floats (``365.0``); JSON tools that rewrite the file, such as
    a release bot bumping its version, print them as ``365``. Writing them that way keeps the
    committed spec stable."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _whole_numbers_as_integers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_whole_numbers_as_integers(item) for item in value]
    return value


def render_openapi(app: FastAPI) -> str:
    """The spec as committed: sorted keys, whole numbers as integers, and text as UTF-8 rather than
    ``\\u`` escapes, as JSON tools that rewrite the file (a release bot) write it."""
    spec = _whole_numbers_as_integers(app.openapi())
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


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
                clock=utc_now,
                demo_data_allowed=settings.environment in DEMO_ENVIRONMENTS,
            )
        finally:
            await engine.dispose()

    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
