"""Shared logging helpers for service runtime logs."""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, TextIO

_RESERVED_RECORD_KEYS = frozenset(
    logging.LogRecord(
        name="",
        level=0,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__
)


class JsonFormatter(logging.Formatter):
    """Format LogRecord instances as one JSON object per line."""

    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "severity": record.levelname,
            "service": self.service,
            "environment": self.environment,
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
            "logger": record.name,
        }
        payload.update(_extra_fields(record))

        if record.exc_info:
            exc_type, exc_value, exc_traceback = record.exc_info
            payload["error_type"] = exc_type.__name__ if exc_type else None
            payload["exception"] = _format_exception(exc_type, exc_value, exc_traceback)
        if record.stack_info:
            payload["stack"] = record.stack_info

        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


def configure_logging(
    *,
    service: str,
    level: str | int = "INFO",
    environment: str | None = None,
    json_logs: bool | None = None,
    stream: TextIO | None = None,
) -> None:
    """Configure root logging for service entrypoints.

    JSON is enabled by default. Set ``JSON_LOGS=false`` to keep the previous
    local-friendly text format during manual debugging.
    """

    resolved_environment = environment or _environment_from_env()
    use_json = _json_logs_enabled(json_logs)
    handler = logging.StreamHandler(stream or sys.stderr)
    if use_json:
        handler.setFormatter(JsonFormatter(service=service, environment=resolved_environment))
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def event_extra(event: str, **fields: object) -> dict[str, object]:
    """Build ``extra`` with the common event field."""

    if not event:
        raise ValueError("event must not be empty")
    return {"event": event, **fields}


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _RESERVED_RECORD_KEYS or key.startswith("_"):
            continue
        out[key] = value
    return out


def _format_exception(
    exc_type: type[BaseException] | None,
    exc_value: BaseException | None,
    exc_traceback: TracebackType | None,
) -> str:
    return "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)).rstrip()


def _environment_from_env() -> str:
    return os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or os.getenv("NODE_ENV") or "dev"


def _json_logs_enabled(value: bool | None) -> bool:
    if value is not None:
        return value
    raw_json_logs = os.getenv("JSON_LOGS")
    if raw_json_logs is not None:
        return raw_json_logs.strip().lower() in {"1", "true", "yes", "on"}
    raw = os.getenv("LOG_FORMAT", "json").strip().lower()
    return raw not in {"text", "plain", "pretty"}


__all__ = ["JsonFormatter", "configure_logging", "event_extra"]
