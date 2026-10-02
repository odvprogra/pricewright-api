"""Migrations are reversible and match the models (HANDBOOK §12, Definition of Done)."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.integration

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config(toml_file=str(PYPROJECT))
    config.attributes["database_url"] = database_url
    return config


def test_migrations_upgrade_downgrade_and_upgrade_again(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")


def test_models_and_migrations_are_in_sync(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")

    command.check(alembic_config)  # raises if autogenerate would emit operations
