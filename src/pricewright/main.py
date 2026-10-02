"""Composition root: the only module that reads settings and wires adapters together."""

import json
from functools import partial
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI

from pricewright import __version__
from pricewright.api.app import create_app
from pricewright.infrastructure.database import create_engine, ping
from pricewright.infrastructure.logging import configure_logging
from pricewright.settings import LogFormat, Settings


def build_app(settings: Settings) -> FastAPI:
    """Wire the application for the given settings."""
    configure_logging(settings.log_level, json=settings.log_format is LogFormat.JSON)
    engine = create_engine(settings.database_url.get_secret_value())
    return create_app(
        title=settings.service_name,
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


if __name__ == "__main__":
    main()
