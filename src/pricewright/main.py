"""Composition root: the only module that reads settings and wires adapters together."""

import asyncio
import json
import sys
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from functools import partial
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from pricewright import __version__, cli
from pricewright.api.app import create_app
from pricewright.api.dependencies import Services
from pricewright.application.housekeeping import (
    expire_offers,
    purge_delivered_events,
    purge_idempotency_keys,
)
from pricewright.application.outbox import Consumer, relay_events
from pricewright.application.ports import UnitOfWork, UnitOfWorkFactory
from pricewright.domain.events import Event, EventType
from pricewright.infrastructure.clock import utc_now
from pricewright.infrastructure.database import create_engine, create_session_factory, ping
from pricewright.infrastructure.documents import WeasyPrintQuoteDocuments
from pricewright.infrastructure.logging import configure_logging
from pricewright.infrastructure.passwords import Argon2PasswordHasher
from pricewright.infrastructure.tokens import JwtAccessTokens
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from pricewright.infrastructure.worker import PeriodicTask, Worker, stop_on_signals
from pricewright.settings import Environment, LogFormat, Settings

DEMO_ENVIRONMENTS = frozenset({Environment.LOCAL, Environment.TEST})
"""Where ``pricewright-admin seed`` may load demo users with their published passphrase."""
EXPIRY_INTERVAL = timedelta(minutes=10)
"""How often the worker persists expired offers; correctness never waits for it (ADR-0005)."""
PURGE_INTERVAL = timedelta(hours=1)
"""How often the worker deletes expired idempotency keys, which already count as absent."""
RELAY_INTERVAL = timedelta(seconds=1)
"""How often the worker relays the outbox's due events (ADR-0008)."""
DELIVERED_PURGE_INTERVAL = timedelta(days=1)
"""How often the worker deletes events delivered a week ago."""


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
        documents=WeasyPrintQuoteDocuments(),
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


def job(event: str, run: Callable[[], Awaitable[int]]) -> Callable[[], Awaitable[None]]:
    """One run of a worker job: an id of its own, which everything it logs and audits carries as
    its request id (ADR-0026), and one line saying how many records it changed, if any."""

    async def run_once() -> None:
        with structlog.contextvars.bound_contextvars(request_id=str(uuid.uuid7())):
            count = await run()
            if count:
                structlog.get_logger(__name__).info(event, count=count)

    return run_once


def traced(consumer: Consumer) -> Consumer:
    """A consumer whose log lines carry the event's id, type and request id, so they join the
    API call that caused it (ADR-0008); it logs the delivery, or the failure it lets through."""

    async def consume(event: Event) -> None:
        with structlog.contextvars.bound_contextvars(
            request_id=event.request_id, event_id=str(event.id), event_type=event.type.value
        ):
            try:
                await consumer(event)
            except Exception:
                structlog.get_logger(__name__).exception("event.failed")
                raise
            structlog.get_logger(__name__).info("event.delivered")

    return consume


def event_consumers() -> dict[EventType, Consumer]:
    """What reacts to each event type; a type without one is delivered at once (ADR-0008)."""
    return {}


def worker_tasks(engine: AsyncEngine) -> list[PeriodicTask]:
    """The worker's jobs (ADR-0026); a failed run is logged and tried again at the next turn."""
    unit_of_work = units_of_work(engine)
    expire = partial(expire_offers, unit_of_work=unit_of_work, clock=utc_now)
    purge_keys = partial(purge_idempotency_keys, unit_of_work=unit_of_work, clock=utc_now)
    consumers = {kind: traced(consumer) for kind, consumer in event_consumers().items()}
    relay = partial(relay_events, unit_of_work=unit_of_work, consumers=consumers, clock=utc_now)
    purge_events = partial(purge_delivered_events, unit_of_work=unit_of_work, clock=utc_now)
    return [
        PeriodicTask("relay_events", RELAY_INTERVAL, job("events.relayed", relay)),
        PeriodicTask("expire_offers", EXPIRY_INTERVAL, job("offers.expired", expire)),
        PeriodicTask(
            "purge_idempotency_keys",
            PURGE_INTERVAL,
            job("idempotency_keys.purged", purge_keys),
        ),
        PeriodicTask(
            "purge_delivered_events",
            DELIVERED_PURGE_INTERVAL,
            job("events.purged", purge_events),
        ),
    ]


async def run_worker(settings: Settings, stop: asyncio.Event) -> None:
    """Run the worker's tasks until ``stop`` is set."""
    engine = create_engine(settings.database_url.get_secret_value())
    try:
        await Worker(worker_tasks(engine)).run(stop)
    finally:
        await engine.dispose()


def worker() -> None:
    """Start the background worker (``pricewright-api-worker``); SIGINT or SIGTERM stop it."""
    settings = Settings()
    configure_logging(settings.log_level, json=settings.log_format is LogFormat.JSON)

    async def until_stopped() -> None:
        stop = asyncio.Event()
        stop_on_signals(stop)
        await run_worker(settings, stop)

    asyncio.run(until_stopped())


if __name__ == "__main__":
    main()
