"""Log lines must be machine-parseable and always carry the correlation id."""

from __future__ import annotations

import json
import logging

import pytest

from app.core.correlation import reset_correlation_id, set_correlation_id
from app.core.logging import ConsoleFormatter, JsonFormatter, configure_logging


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="provisioning step %s finished",
        args=("purchase_number",),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_one_object() -> None:
    payload = json.loads(JsonFormatter().format(_record()))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "provisioning step purchase_number finished"
    assert payload["timestamp"].endswith("+00:00")


def test_json_formatter_includes_extra_fields() -> None:
    payload = json.loads(JsonFormatter().format(_record(tenant_id="t-1", attempt=2)))
    assert payload["tenant_id"] == "t-1"
    assert payload["attempt"] == 2


def test_json_formatter_includes_the_correlation_id() -> None:
    token = set_correlation_id("run-42")
    try:
        payload = json.loads(JsonFormatter().format(_record()))
    finally:
        reset_correlation_id(token)
    assert payload["correlation_id"] == "run-42"


def test_json_formatter_omits_the_id_outside_a_request() -> None:
    assert "correlation_id" not in json.loads(JsonFormatter().format(_record()))


def test_json_formatter_renders_exceptions() -> None:
    try:
        raise ValueError("vendor said no")
    except ValueError:
        import sys

        record = _record()
        record.exc_info = sys.exc_info()

    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: vendor said no" in payload["exception"]


def test_json_formatter_drops_uvicorn_colour_noise() -> None:
    """uvicorn attaches an ANSI copy of every message; it must not reach the logs."""
    payload = json.loads(JsonFormatter().format(_record(color_message="[36mhi[0m")))
    assert "color_message" not in payload


def test_json_formatter_survives_unserialisable_values() -> None:
    payload = json.loads(JsonFormatter().format(_record(obj=object())))
    assert payload["obj"].startswith("<object object")


def test_console_formatter_is_readable() -> None:
    token = set_correlation_id("abcdef0123456789")
    try:
        line = ConsoleFormatter().format(_record(tenant_id="t-1"))
    finally:
        reset_correlation_id(token)

    assert "INFO" in line
    assert "[abcdef01]" in line
    assert "tenant_id=t-1" in line


@pytest.mark.parametrize("fmt", ["json", "console"])
def test_configure_logging_installs_exactly_one_handler(fmt: str) -> None:
    configure_logging(level="INFO", fmt=fmt)
    configure_logging(level="INFO", fmt=fmt)

    root = logging.getLogger()
    assert len(root.handlers) == 1
    expected = JsonFormatter if fmt == "json" else ConsoleFormatter
    assert isinstance(root.handlers[0].formatter, expected)
