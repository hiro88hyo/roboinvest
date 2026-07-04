from __future__ import annotations

import hashlib
import json
from typing import Any

from trade_contracts.event_research import EventRecord, ObservationRecord

PROMPT_VERSION = "event_ai_label_v0"
FORBIDDEN_PROMPT_KEYS = {
    "forward_return",
    "exit_price",
    "pnl",
    "trade_result",
    "validation_label",
    "random_percentile",
}


def build_event_prompt(event: EventRecord, observation: ObservationRecord) -> str:
    payload = {
        "prompt_version": PROMPT_VERSION,
        "task": "Classify the disclosed event. Do not recommend BUY, SELL, or HOLD.",
        "event": {
            "event_id": event.event_id,
            "symbol": event.symbol,
            "event_type": event.event_type.value,
            "raw_document_type": event.raw_document_type,
            "disclosed_at": event.disclosed_at.isoformat(),
            "data_available_at": event.data_available_at.isoformat(),
            "feature_cutoff_at": event.feature_cutoff_at.isoformat(),
        },
        "official_numeric_summary": _official_numeric_summary(event.raw),
        "fundamental_features_v0": observation.fundamental_features_v0.model_dump(mode="json"),
        "valuation_features_v0": observation.valuation_features_v0.model_dump(mode="json"),
        "technical_context_v0": observation.technical_context_v0.model_dump(mode="json"),
        "allowed_output_schema": {
            "event_type": (
                "forecast_revision|dividend_revision|earnings_result|buyback_announcement"
            ),
            "fundamental_direction": "positive|negative|mixed|neutral|unclear",
            "fundamental_strength": "integer 0..3",
            "revision_quality": "high|medium|low|unclear",
            "valuation_context": "cheap|fair|expensive|invalid|unclear",
            "technical_context": "favorable|neutral|extended|high_risk|unclear",
            "expected_horizon": "2d|5d|10d|20d|avoid|unclear",
            "risk_flags": "array of strings",
            "confidence": "number 0..1",
            "rationale": "short explanation",
        },
    }
    _assert_no_forbidden_keys(payload)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _official_numeric_summary(raw: dict[str, Any]) -> dict[str, Any]:
    allowlist = (
        "FEPS",
        "FOP",
        "FNP",
        "FSales",
        "FDivAnn",
        "EPS",
        "BPS",
        "CurFYEn",
        "NxtFYEn",
        "DocType",
        "DiscDate",
        "DiscTime",
        "DiscNo",
    )
    return {key: raw.get(key) for key in allowlist if raw.get(key) not in (None, "")}


def _assert_no_forbidden_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(forbidden in lowered for forbidden in FORBIDDEN_PROMPT_KEYS):
                raise ValueError(f"forbidden prompt key: {key}")
            _assert_no_forbidden_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_forbidden_keys(item)
