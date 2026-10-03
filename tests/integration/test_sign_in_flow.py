"""Sign in through the fully wired application: PostgreSQL, argon2id and real JWTs."""

import secrets
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from pricewright.application.onboarding import RegisterTenant, register_tenant
from pricewright.infrastructure.database import create_engine
from pricewright.infrastructure.passwords import Argon2PasswordHasher
from pricewright.main import build_app, units_of_work
from pricewright.settings import Settings

pytestmark = pytest.mark.integration

PASSWORD = "northfield admin passphrase"


@pytest.fixture
async def app(migrated_database_url: str) -> AsyncIterator[FastAPI]:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(migrated_database_url),
        jwt_secret=SecretStr(secrets.token_urlsafe(32)),
    )
    engine = create_engine(migrated_database_url)
    await register_tenant(
        RegisterTenant(
            name="Northfield Supply",
            currency="USD",
            tax_rate=Decimal("0.07"),
            admin_email="avery@northfield.example",
            admin_full_name="Avery Admin",
            admin_password=PASSWORD,
        ),
        unit_of_work=units_of_work(engine),
        hasher=Argon2PasswordHasher(),
    )
    await engine.dispose()
    app = build_app(settings)
    yield app
    async with app.router.lifespan_context(app):
        pass  # runs the shutdown hooks: disposes the app's engine


@pytest.mark.usefixtures("session_factory")  # empties the tables afterwards
async def test_sign_in_then_read_the_signed_in_user(app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "avery@northfield.example", "password": PASSWORD},
        )
        token = login.json()["access_token"]
        me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert login.status_code == 200
    assert me.status_code == 200
    assert me.json()["email"] == "avery@northfield.example"
    assert me.json()["role"] == "admin"
