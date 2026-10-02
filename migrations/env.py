"""Alembic environment: migrations run with the service's own settings and metadata.

The database URL comes from ``Settings`` (``DATABASE_URL``) unless a caller passes one through
``config.attributes["database_url"]``, as the migration tests do.
"""

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from pricewright.infrastructure.database import Base
from pricewright.infrastructure.logging import configure_logging
from pricewright.settings import LogFormat, Settings

target_metadata = Base.metadata


def _database_url() -> str:
    url: str | None = context.config.attributes.get("database_url")
    if url is None:
        settings = Settings()
        if context.config.cmd_opts is not None:  # invoked from the CLI, not from tests
            configure_logging(settings.log_level, json=settings.log_format is LogFormat.JSON)
        url = settings.database_url.get_secret_value()
    return url


def _run_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run_with_connection)
    await engine.dispose()


if context.is_offline_mode():
    _run_offline()
else:
    asyncio.run(_run_online())
