"""Service configuration, read from environment variables (12-factor) and an optional ``.env``."""

import secrets
from datetime import timedelta
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

_ASYNC_DRIVER_SCHEME = "postgresql+asyncpg://"
# RFC 7518 §3.2: an HS256 key must have at least 256 bits.
MIN_JWT_SECRET_LENGTH = 32
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
    # Unset locally, a random secret per process: nothing to configure, and no secret in the repo.
    jwt_secret: SecretStr = Field(default_factory=lambda: SecretStr(secrets.token_urlsafe(32)))
    access_token_ttl_seconds: int = Field(default=15 * 60, ge=60, le=60 * 60)

    @field_validator("database_url")
    @classmethod
    def _use_async_driver(cls, value: SecretStr) -> SecretStr:
        """Accept the plain ``postgres://`` URLs that hosting providers hand out."""
        url = value.get_secret_value()
        for scheme in _PLAIN_SCHEMES:
            if url.startswith(scheme):
                return SecretStr(_ASYNC_DRIVER_SCHEME + url.removeprefix(scheme))
        return value

    @field_validator("jwt_secret")
    @classmethod
    def _long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(f"must have at least {MIN_JWT_SECRET_LENGTH} characters")
        return value

    @model_validator(mode="after")
    def _deployed_environments_set_the_jwt_secret(self) -> Self:
        """A per-process secret would sign tokens that other replicas reject."""
        deployed = self.environment in {Environment.STAGING, Environment.PRODUCTION}
        if deployed and "jwt_secret" not in self.model_fields_set:
            raise ValueError("JWT_SECRET is required in staging and production")
        return self

    @property
    def access_token_ttl(self) -> timedelta:
        return timedelta(seconds=self.access_token_ttl_seconds)
