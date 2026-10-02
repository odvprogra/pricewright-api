"""Structured logging: JSON lines when deployed, readable console output for local development.

Every line carries a UTC timestamp, the level, the logger name and any context bound with
``structlog.contextvars.bind_contextvars`` (for example ``request_id``). Records from the standard
library (uvicorn, SQLAlchemy, ...) go through the same pipeline, so the output has one format.
"""

import logging
import sys
from typing import TextIO

import structlog
from structlog.typing import Processor


def configure_logging(level: str, *, json: bool, stream: TextIO | None = None) -> None:
    """Configure structlog and the standard library root logger. Call once, at startup."""
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: list[Processor] = (
        [structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()]
        if json
        else [structlog.dev.ConsoleRenderer()]
    )
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *renderer],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
