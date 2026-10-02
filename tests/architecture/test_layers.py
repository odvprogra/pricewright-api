"""Architecture tests: the dependency rule (HANDBOOK §5) is checked on every test run.

The contracts live in ``[tool.importlinter]`` in pyproject.toml.
"""

from importlinter.cli import EXIT_STATUS_SUCCESS, lint_imports


def test_import_contracts_hold() -> None:
    assert lint_imports(no_cache=True, no_logo=True) == EXIT_STATUS_SUCCESS
