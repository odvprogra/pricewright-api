"""``pricewright-admin seed`` wired for real: PostgreSQL, argon2id and the settings (ADR-0024).

The demo data is loaded once for the module (each load takes seconds); every test leaves the
database holding the same data, and the module empties it at the end.
"""

import asyncio
import sys
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from pricewright.application.ports import UnitOfWork
from pricewright.demo.seed import DEMO_PASSWORD, seed_demo
from pricewright.infrastructure.database import Base, create_engine
from pricewright.infrastructure.passwords import Argon2PasswordHasher
from pricewright.main import admin, units_of_work
from tests.demo_snapshot import Picture, picture
from tests.fakes import FakePasswordHasher, FakeUnitOfWork, InMemoryDatabase

pytestmark = pytest.mark.integration

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
AS_OF, SEED = "2026-10-01", "7"
ADMINS = ("avery@northfield.example", "robin@larkspur.example")


def _admin(database_url: str, environment: str) -> int | str | None:
    """Run the console script in-process and return its exit code."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("DATABASE_URL", database_url)
        monkeypatch.setenv("ENVIRONMENT", environment)
        monkeypatch.setenv("JWT_SECRET", "x" * 32)  # deployed environments require one
        argv = ["pricewright-admin", "seed", "--as-of", AS_OF, "--seed", SEED]
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as exit_info:
            admin()
    return exit_info.value.code


async def _seeded(database_url: str) -> tuple[list[Picture], bool]:
    """The demo tenants' picture, and whether the demo passphrase opens their admins' accounts."""
    engine = create_engine(database_url)
    try:
        unit_of_work = units_of_work(engine)
        async with unit_of_work() as uow:
            found = [await uow.identities.user_by_email(email) for email in ADMINS]
        admins = [user for user in found if user is not None]
        hasher = Argon2PasswordHasher()
        opens = [await hasher.verify(user.password_hash, DEMO_PASSWORD) for user in admins]
        tenants = await picture(unit_of_work, [user.tenant_id for user in admins])
        return tenants, len(opens) == len(ADMINS) and all(opens)
    finally:
        await engine.dispose()


async def _fake_picture() -> list[Picture]:
    database = InMemoryDatabase()

    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(database)

    report = await seed_demo(
        unit_of_work=unit_of_work,
        hasher=FakePasswordHasher(),
        as_of=date.fromisoformat(AS_OF),
        seed=int(SEED),
    )
    return await picture(unit_of_work, [tenant.tenant_id for tenant in report.tenants])


async def _empty(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
        async with engine.begin() as connection:
            # Table names come from our own metadata, never from input.
            await connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    finally:
        await engine.dispose()


# Synchronous on purpose: the command runs its own event loop, like the real console script.
@pytest.fixture(scope="module")
def seeded_url(migrated_database_url: str) -> Iterator[str]:
    assert _admin(migrated_database_url, "local") == 0
    yield migrated_database_url
    asyncio.run(_empty(migrated_database_url))


@pytest.fixture(scope="module")
def expected() -> list[Picture]:
    return asyncio.run(_fake_picture())


def test_seed_loads_into_postgresql_exactly_what_it_loads_into_the_fakes(
    seeded_url: str, expected: list[Picture]
) -> None:
    seeded, passphrase_opens = asyncio.run(_seeded(seeded_url))

    assert seeded == expected
    assert passphrase_opens


def test_seed_refuses_a_second_load_and_leaves_the_database_as_it_was(
    seeded_url: str, expected: list[Picture]
) -> None:
    exit_code = _admin(seeded_url, "local")

    assert exit_code == 1
    assert asyncio.run(_seeded(seeded_url))[0] == expected


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_seed_refuses_deployed_environments_whatever_the_database_holds(
    seeded_url: str, expected: list[Picture], environment: str
) -> None:
    exit_code = _admin(seeded_url, environment)

    assert exit_code == 1
    assert asyncio.run(_seeded(seeded_url))[0] == expected


def test_seed_after_a_reset_loads_the_same_data_again(
    seeded_url: str, expected: list[Picture]
) -> None:
    """What ``just seed`` does: migrate down to nothing, up again, and load."""
    config = Config(toml_file=str(PYPROJECT))
    config.attributes["database_url"] = seeded_url
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    exit_code = _admin(seeded_url, "test")

    assert exit_code == 0
    assert asyncio.run(_seeded(seeded_url))[0] == expected
