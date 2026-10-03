import secrets
from datetime import timedelta

import pytest
from pydantic import ValidationError

from pricewright.settings import MIN_JWT_SECRET_LENGTH, Environment, LogFormat, Settings


def test_settings_without_environment_uses_deployable_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("ENVIRONMENT", "LOG_LEVEL", "LOG_FORMAT"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.environment is Environment.LOCAL
    assert settings.log_level == "INFO"
    assert settings.log_format is LogFormat.JSON


def test_settings_reads_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("JWT_SECRET", secrets.token_urlsafe(32))

    settings = Settings(_env_file=None)

    assert settings.environment is Environment.PRODUCTION
    assert settings.log_level == "WARNING"


def test_settings_invalid_value_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "LOUD")

    with pytest.raises(ValidationError, match="log_level"):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "url",
    [
        "postgres://user:pw@db.example.com:25060/app",
        "postgresql://user:pw@db.example.com:25060/app",
        "postgresql+asyncpg://user:pw@db.example.com:25060/app",
    ],
)
def test_settings_database_url_always_uses_the_async_driver(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", url)

    settings = Settings(_env_file=None)

    assert (
        settings.database_url.get_secret_value()
        == "postgresql+asyncpg://user:pw@db.example.com:25060/app"
    )


def test_settings_never_expose_the_database_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:s3cret@db/app")

    settings = Settings(_env_file=None)

    assert "s3cret" not in repr(settings)
    assert "s3cret" not in str(settings.model_dump())


def test_settings_generate_a_jwt_secret_per_process_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)

    first, second = Settings(_env_file=None), Settings(_env_file=None)

    assert len(first.jwt_secret.get_secret_value()) >= MIN_JWT_SECRET_LENGTH
    assert first.jwt_secret != second.jwt_secret


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_settings_require_a_jwt_secret_when_deployed(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.delenv("JWT_SECRET", raising=False)

    with pytest.raises(ValidationError, match="JWT_SECRET is required"):
        Settings(_env_file=None)


def test_settings_reject_a_short_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "x" * (MIN_JWT_SECRET_LENGTH - 1))

    with pytest.raises(ValidationError, match="jwt_secret"):
        Settings(_env_file=None)


def test_settings_access_token_ttl_is_fifteen_minutes_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ACCESS_TOKEN_TTL_SECONDS", raising=False)

    assert Settings(_env_file=None).access_token_ttl == timedelta(minutes=15)
