"""Structured logging.

Two formatters over the standard library — no logging dependency is worth adding
for this. ``json`` is what runs anywhere the logs are collected; ``console`` is
for a human watching a terminal. Both stamp the current correlation id, so a
single grep reconstructs one provisioning run end to end.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.correlation import get_correlation_id

# Attributes the stdlib puts on every LogRecord. Derived from a throwaway record
# rather than hard-coded, so a new Python release cannot silently start leaking
# an internal attribute into our structured output. `message` and `asctime` are
# added later by Formatter itself, and `color_message` is an ANSI-coloured copy
# uvicorn attaches to its own records, so those are excluded explicitly.
_RESERVED: frozenset[str] = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "color_message",
}


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in record.__dict__.items() if k not in _RESERVED}


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        correlation_id = get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id

        payload.update(_extra_fields(record))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Readable single line for local development."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S")
        correlation_id = get_correlation_id()
        prefix = f"[{correlation_id[:8]}] " if correlation_id else ""

        line = f"{timestamp} {record.levelname:<8} {prefix}{record.name}: {record.getMessage()}"

        extras = _extra_fields(record)
        if extras:
            line += " | " + " ".join(f"{k}={v}" for k, v in sorted(extras.items()))
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    """Install a single stdout handler on the root logger.

    Idempotent: calling it again replaces the handler rather than adding a
    second one, so repeated app construction in tests cannot duplicate output.
    """
    formatter: logging.Formatter = JsonFormatter() if fmt == "json" else ConsoleFormatter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
        existing.close()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own handlers; drop them so everything is formatted
    # once, by us, and appears in the same stream.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    """Module-level logger accessor, so call sites do not import ``logging``."""
    return logging.getLogger(name)
