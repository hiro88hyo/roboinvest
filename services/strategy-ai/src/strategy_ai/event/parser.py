from __future__ import annotations

import json

from pydantic import ValidationError
from trade_contracts.event_research import EventAiLabel


class EventAiParseError(ValueError):
    """Invalid event AI label. Callers fail closed and do not trade."""


def parse_event_ai_label(raw: str) -> EventAiLabel:
    raw = _strip_json_code_fence(raw)
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


def _strip_json_code_fence(raw: str) -> str:
    stripped = raw.strip()
    if not stripped.startswith("```"):
        return raw
    lines = stripped.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return raw
    opener = lines[0].strip().lower()
    if opener not in {"```json", "```"}:
        return raw
    return "\n".join(lines[1:-1]).strip()
