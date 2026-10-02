import logging
from collections.abc import Iterator

import pytest
import structlog


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """Undo any logging configuration a test applies, so tests stay independent.

    Besides handlers and levels this restores each logger's ``disabled`` flag: tools that call
    ``logging.config.dictConfig`` (import-linter in the architecture test does) disable every logger
    that already exists, which would silently drop records in later tests.
    """
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    disabled = {
        logger: logger.disabled
        for logger in logging.root.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    }
    yield
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    root.handlers, root.level = handlers, level
    for logger, was_disabled in disabled.items():
        logger.disabled = was_disabled
