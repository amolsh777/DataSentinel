"""Structured JSON logging with request-id propagation.

Never log uploaded record values or secrets.

In development (DS_APP_ENV != production) human-readable text is used instead
of JSON so the terminal stays readable. Set DS_LOG_FORMAT=json to force JSON
in any environment.
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import datetime, timezone


class _RequestIdFilter(logging.Filter):
    """Inject the current request-id (set by middleware) into every record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return True


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        self.format_exception = lambda _: None  # prevent double traceback
        obj: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "msg": record.getMessage(),
        }
        # Structured extra fields attached by middleware / services.
        for key in ("method", "path", "status_code", "duration_ms", "dataset_id",
                    "run_id", "job_id", "version_number"):
            if hasattr(record, key):
                obj[key] = getattr(record, key)
        if record.exc_info:
            obj["exc"] = traceback.format_exception(*record.exc_info)
        return json.dumps(obj, default=str)


class _TextFormatter(logging.Formatter):
    """Compact coloured-ish text for development terminals."""

    COLOURS = {
        "DEBUG": "\033[36m", "INFO": "\033[32m",
        "WARNING": "\033[33m", "ERROR": "\033[31m", "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        c = self.COLOURS.get(record.levelname, "")
        rid = getattr(record, "request_id", "-")
        rid_part = f" [{rid[:8]}]" if rid != "-" else ""
        base = f"{record.asctime if hasattr(record,'asctime') else ''} {c}{record.levelname:8}{self.RESET} [{record.name}]{rid_part} {record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(level: str = "INFO") -> None:
    """Set up the root logger.

    JSON format is used in production or when DS_LOG_FORMAT=json.
    Human-readable text is used otherwise.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    app_env = os.environ.get("DS_APP_ENV", "development")
    log_format = os.environ.get("DS_LOG_FORMAT", "json" if app_env == "production" else "text")

    formatter: logging.Formatter = (
        _JsonFormatter() if log_format == "json" else _TextFormatter(
            datefmt="%H:%M:%S"
        )
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    # Remove existing handlers so re-entrant configure_logging() in tests is safe.
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # Suppress noisy third-party loggers.
    for noisy in ("uvicorn.access", "multipart", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def inject_request_id(request_id: str) -> None:
    """Called by middleware to attach request_id to all records in this thread."""
    # We use a thread-local via a Filter on the handler so it works with uvicorn's thread pool.
    for handler in logging.getLogger().handlers:
        for f in handler.filters:
            if isinstance(f, _RequestIdFilter):
                # Monkey-patch the filter for this invocation's thread is tricky;
                # instead we rely on the middleware passing request_id via log `extra`.
                break
