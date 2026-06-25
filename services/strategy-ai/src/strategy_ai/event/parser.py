from __future__ import annotations

import json

from pydantic import ValidationError
from trade_contracts.event_research import EventAiLabel


class EventAiParseError(ValueError):
    """Invalid event AI label. Callers fail closed and do not trade."""


def parse_event_ai_label(raw: str) -> EventAiLabel:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EventAiParseError("invalid json") from exc
    if _contains_non_finite(payload):
        raise EventAiParseError("non-finite value")
    try:
        return EventAiLabel.model_validate(payload)
    except ValidationError as exc:
        raise EventAiParseError(str(exc)) from exc


def _contains_non_finite(value: object) -> bool:
    if isinstance(value, float):
        return value != value or value in (float("inf"), float("-inf"))
    if isinstance(value, dict):
        return any(_contains_non_finite(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_non_finite(item) for item in value)
    return False
