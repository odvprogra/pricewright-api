"""``pricewright-admin`` wired for real: settings, PostgreSQL and argon2id."""

import asyncio
import io
import sys

import pytest

from pricewright.domain.users import Role, User
from pricewright.infrastructure.database import create_engine, create_session_factory
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from pricewright.main import admin

pytestmark = pytest.mark.integration


async def find_user(database_url: str, email: str) -> User | None:
    engine = create_engine(database_url)
    try:
        async with SqlAlchemyUnitOfWork(create_session_factory(engine)) as uow:
            return await uow.identities.user_by_email(email)
    finally:
        await engine.dispose()


# Synchronous on purpose: the command runs its own event loop, like the real console script.
# session_factory empties the tables afterwards.
@pytest.mark.usefixtures("session_factory")
def test_admin_create_tenant_registers_the_tenant_and_its_admin(
    migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", migrated_database_url)
    monkeypatch.setattr(sys, "stdin", io.StringIO("larkspur admin passphrase\n"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pricewright-admin",
            "create-tenant",
            "--name=Larkspur Tool Co.",
            "--currency=USD",
            "--tax-rate=0.06",
            "--admin-email=robin@larkspur.example",
            "--admin-name=Robin Admin",
            "--password-stdin",
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        admin()

    robin = asyncio.run(find_user(migrated_database_url, "robin@larkspur.example"))
    assert exit_info.value.code == 0
    assert robin is not None
    assert robin.role is Role.ADMIN
    assert robin.password_hash.startswith("$argon2id$")
