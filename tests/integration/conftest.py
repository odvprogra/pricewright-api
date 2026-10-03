import secrets
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

import pricewright.infrastructure.records  # noqa: F401 - registers the tables in Base.metadata
from pricewright.application.onboarding import RegisterTenant, register_tenant
from pricewright.infrastructure.database import Base, create_engine, create_session_factory
from pricewright.infrastructure.passwords import Argon2PasswordHasher
from pricewright.main import build_app, units_of_work
from pricewright.settings import Settings
from tests.integration.data import ADMIN_EMAIL, ADMIN_PASSWORD

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


@pytest.fixture
async def northfield_client(migrated_database_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """The fully wired application (PostgreSQL, argon2id, JWTs) with Northfield and its admin.

    Tests using it also request ``session_factory``, which empties the tables afterwards.
    """
    engine = create_engine(migrated_database_url)
    await register_tenant(
        RegisterTenant(
            name="Northfield Supply",
            currency="USD",
            tax_rate=Decimal("0.07"),
            admin_email=ADMIN_EMAIL,
            admin_full_name="Avery Admin",
            admin_password=ADMIN_PASSWORD,
        ),
        unit_of_work=units_of_work(engine),
        hasher=Argon2PasswordHasher(),
    )
    await engine.dispose()
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(migrated_database_url),
        jwt_secret=SecretStr(secrets.token_urlsafe(32)),
    )
    app = build_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    async with app.router.lifespan_context(app):
        pass  # runs the shutdown hooks: disposes the app's engine
