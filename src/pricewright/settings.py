"""Service configuration, read from environment variables (12-factor) and an optional ``.env``."""

from enum import StrEnum
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

_ASYNC_DRIVER_SCHEME = "postgresql+asyncpg://"
_PLAIN_SCHEMES = ("postgresql://", "postgres://")


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


class Settings(BaseSettings):
    """Validated settings: an invalid value stops the service at startup, not at first use."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    service_name: str = "pricewright-api"
    environment: Environment = Environment.LOCAL
    log_level: LogLevel = "INFO"
    log_format: LogFormat = LogFormat.JSON
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    # Matches docker-compose.yml for local development; deployed environments set DATABASE_URL.
    database_url: SecretStr = SecretStr("postgresql+asyncpg://app:app@localhost:5432/pricewright")

    @field_validator("database_url")
    @classmethod
    def _use_async_driver(cls, value: SecretStr) -> SecretStr:
        """Accept the plain ``postgres://`` URLs that hosting providers hand out."""
        url = value.get_secret_value()
        for scheme in _PLAIN_SCHEMES:
            if url.startswith(scheme):
                return SecretStr(_ASYNC_DRIVER_SCHEME + url.removeprefix(scheme))
        return value
