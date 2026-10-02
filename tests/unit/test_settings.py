import pytest
from pydantic import ValidationError

from pricewright.settings import Environment, LogFormat, Settings


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
