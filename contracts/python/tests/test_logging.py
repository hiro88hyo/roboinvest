from __future__ import annotations

import io
import json
import logging

import pytest
from trade_contracts.logging import JsonFormatter, configure_logging, event_extra


def test_json_formatter_outputs_common_fields_and_extra() -> None:
    record = logging.LogRecord(
        name="svc.module",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    record.event = "service_started"
    record.symbol = "7203"

    line = JsonFormatter(service="gateway", environment="production").format(record)

    payload = json.loads(line)
    assert payload["severity"] == "INFO"
    assert payload["service"] == "gateway"
    assert payload["environment"] == "production"
    assert payload["event"] == "service_started"
    assert payload["message"] == "hello world"
    assert payload["logger"] == "svc.module"
    assert payload["symbol"] == "7203"
    assert "timestamp" in payload


def test_configure_logging_defaults_to_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    stream = io.StringIO()
    configure_logging(service="feeder", level="INFO", stream=stream)

    logging.getLogger("example").info(
        "published",
        extra=event_extra("order_published", order_id="order-1", quantity=100),
    )

    payload = json.loads(stream.getvalue())
    assert payload["service"] == "feeder"
    assert payload["environment"] == "test"
    assert payload["event"] == "order_published"
    assert payload["order_id"] == "order-1"
    assert payload["quantity"] == 100


def test_configure_logging_can_keep_text_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JSON_LOGS", "false")
    stream = io.StringIO()
    configure_logging(service="feeder", level="INFO", stream=stream)

    logging.getLogger("example").info("plain message")

    assert "plain message" in stream.getvalue()
    assert not stream.getvalue().lstrip().startswith("{")


def test_configure_logging_defaults_environment_to_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("NODE_ENV", raising=False)
    stream = io.StringIO()
    configure_logging(service="feeder", level="INFO", stream=stream)

    logging.getLogger("example").info("hello")

    payload = json.loads(stream.getvalue())
    assert payload["environment"] == "dev"


def test_event_extra_requires_event_name() -> None:
    with pytest.raises(ValueError, match="event"):
        event_extra("")
