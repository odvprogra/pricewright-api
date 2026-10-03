"""The migration history stays linear, and its files sort by creation time."""

import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
# file_template in pyproject.toml: <UTC date>_<HHMM>-<revision id>_<slug>.py
FILE_NAME = re.compile(r"\d{4}_\d{2}_\d{2}_\d{4}-[0-9a-f]{12}_\w+\.py")


def test_migration_history_has_a_single_head() -> None:
    scripts = ScriptDirectory.from_config(Config(toml_file=str(ROOT / "pyproject.toml")))

    heads = scripts.get_heads()

    assert len(heads) <= 1, f"two branches add migrations {heads}: run `alembic merge heads`"


def test_migration_files_are_named_by_creation_time() -> None:
    names = [path.name for path in (ROOT / "migrations" / "versions").glob("*.py")]

    assert [name for name in names if not FILE_NAME.fullmatch(name)] == []
