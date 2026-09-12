"""Structured logging.

Deliberately built on the standard library rather than a logging framework:
the whole implementation is ~100 readable lines, has no dependency, and can be
explained line by line.

Two formats:
  * ``json``    - one JSON object per line. Machine-parseable, and what
                  CloudWatch Logs Insights expects in M10.
  * ``console`` - human-readable, for local development.

Every record carries the current request's ``request_id`` (see
``app.core.middleware``), so all log lines produced while handling one request
can be correlated - including lines emitted deep inside a service function that
knows nothing about HTTP.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# ContextVar rather than a global: each asyncio task gets its own value, so
# concurrent requests cannot read each other's request id.
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

# Keys whose values must never reach a log file (Rule 7 / security requirement).
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "jwt_secret",
        "secret",
        "api_key",
        "groq_api_key",
        "aws_secret_access_key",
    }
)

_REDACTED = "***REDACTED***"

# Attributes present on every LogRecord; anything else was passed via `extra=`
# and is therefore structured context worth emitting.
_STANDARD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


def _redact(value: Any, key: str | None = None) -> Any:
    """Recursively replace sensitive values before they are serialised."""
    if key is not None and key.lower() in SENSITIVE_KEYS:
        return _REDACTED
    if isinstance(value, dict):
        return {k: _redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: _redact(val, key)
        for key, val in record.__dict__.items()
        if key not in _STANDARD_ATTRS and not key.startswith("_")
    }


class JsonFormatter(logging.Formatter):
    """Renders each record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if (rid := request_id_ctx.get()) is not None:
            payload["request_id"] = rid

        payload.update(_extras(record))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # default=str so UUIDs/datetimes never crash the logger.
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Compact, aligned, human-readable output for local development."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S")
        rid = request_id_ctx.get()
        rid_part = f" [{rid[:8]}]" if rid else ""

        extras = _extras(record)
        extra_part = ""
        if extras:
            extra_part = "  " + " ".join(f"{k}={v}" for k, v in extras.items())

        line = f"{ts} {record.levelname:<7}{rid_part} {record.name:<28} {record.getMessage()}{extra_part}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install the root handler. Safe to call more than once."""
    formatter: logging.Formatter = JsonFormatter() if fmt == "json" else ConsoleFormatter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn installs its own handlers; clearing them and letting records
    # propagate to root means access logs get the same JSON shape as ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # SQLAlchemy is extremely chatty at INFO; keep it at WARNING unless the
    # application itself is in DEBUG.
    sql_level = logging.INFO if level.upper() == "DEBUG" else logging.WARNING
    logging.getLogger("sqlalchemy.engine").setLevel(sql_level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
