from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from trade_contracts.enums import Action

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ParsedDecision:
    action: Action
    confidence: float
    reasoning: str


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_response(text: str) -> ParsedDecision | None:
    """LLM 応答テキストから `ParsedDecision` を作る。失敗したら None。

    - JSON 直接 / JSON を含む散文 / コードフェンス付きをすべて受ける
    - `action` は大小区別せず BUY / SELL / HOLD のいずれかにマップ
    - `confidence` は 0.0-1.0 にクランプ
    - `reasoning` は欠損時は空文字
    """
    payload = _extract_json(text)
    if payload is None:
        logger.warning("llm response has no JSON object: %r", text[:200])
        return None
    try:
        action = _coerce_action(payload.get("action"))
    except ValueError as exc:
        logger.warning("invalid action: %s payload=%r", exc, payload)
        return None
    confidence = _coerce_confidence(payload.get("confidence"))
    if confidence is None:
        logger.warning("invalid confidence: payload=%r", payload)
        return None
    reasoning = payload.get("reasoning")
    if not isinstance(reasoning, str):
        reasoning = ""
    return ParsedDecision(action=action, confidence=confidence, reasoning=reasoning)


def _extract_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        loaded = json.loads(cleaned)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(text)
        if not match:
            return None
        try:
            loaded = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(loaded, dict):
        return None
    return loaded


def _coerce_action(value: Any) -> Action:
    if not isinstance(value, str):
        raise ValueError(f"action must be string: {value!r}")
    normalised = value.strip().upper()
    if normalised in {a.value for a in Action}:
        return Action(normalised)
    raise ValueError(f"unknown action: {value!r}")


def _coerce_confidence(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        f = float(value)
    elif isinstance(value, str):
        try:
            f = float(value)
        except ValueError:
            return None
    else:
        return None
    if f != f:  # NaN
        return None
    return max(0.0, min(1.0, f))
