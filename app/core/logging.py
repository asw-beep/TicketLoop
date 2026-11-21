"""Structured logging: every log line is one JSON object that carries the request ID.

Two kinds of log calls exist in this process:
- our code calls structlog: `structlog.get_logger(__name__).info("event.name", key=value)`
- libraries (uvicorn, SQLAlchemy) call the standard `logging` module

Both are routed through one stdlib handler whose formatter runs structlog's processors, so
both come out in the same JSON shape. `merge_contextvars` copies whatever the request
middleware bound (the request ID) into every line logged while that request is running.
"""

import logging

import structlog
from structlog.typing import Processor

_HANDLER_NAME = "ticketloop"

# Run on every log line, whether it came from structlog or from the stdlib logging module.
_SHARED_PROCESSORS: list[Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]


def build_formatter(json_logs: bool) -> logging.Formatter:
    """The formatter that turns a log record into its final text: JSON or coloured console."""
    final_processors: list[Processor] = [structlog.stdlib.ProcessorFormatter.remove_processors_meta]
    if json_logs:
        # Turn a traceback into an "exception" string field so it stays inside one JSON line.
        final_processors += [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    else:
        final_processors += [structlog.dev.ConsoleRenderer()]

    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,  # applied to stdlib records only
        processors=final_processors,
    )


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    """Set up structlog and the stdlib root logger. Safe to call more than once."""
    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.processors.StackInfoRenderer(),
            # Hand the event dict to the stdlib handler below instead of rendering it here.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler()
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(build_formatter(json_logs))

    root = logging.getLogger()
    # Replace only our own handler, so a second call does not print every line twice and
    # handlers added by other tools (pytest's log capture) stay in place.
    for existing in list(root.handlers):
        if existing.get_name() == _HANDLER_NAME:
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own plain-text handlers. Remove them so its lines reach our handler.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # Our middleware writes one access line per request with the request ID; uvicorn's own
    # access line would be a duplicate without it.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
    access_logger.disabled = True
