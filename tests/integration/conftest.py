from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

import pricewright.infrastructure.records  # noqa: F401 - registers the tables in Base.metadata
from pricewright.infrastructure.database import Base, create_engine, create_session_factory

# Same major version as docker-compose.yml and production (ADR-0002 in engineering-standards).
POSTGRES_IMAGE = "postgres:18-alpine"
PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A throwaway PostgreSQL for the whole test session (needs Docker)."""
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def migrated_database_url(database_url: str) -> str:
    """The session's database with every migration applied."""
    config = Config(toml_file=str(PYPROJECT))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")
    return database_url


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(
    migrated_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions on the migrated database; every table is emptied after the test."""
    engine = create_engine(migrated_database_url)
    yield create_session_factory(engine)
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    async with engine.begin() as connection:
        # Table names come from our own metadata, never from input.
        await connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    await engine.dispose()
