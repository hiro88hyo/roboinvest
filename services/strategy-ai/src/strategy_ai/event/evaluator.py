from __future__ import annotations

import random
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from trade_contracts.event_research import EntryArm, EventAiLabel, EventType, ObservationRecord


def ai_arm_allows(
    obs: ObservationRecord,
    label: EventAiLabel | None,
    arm: EntryArm,
) -> bool:
    if label is None:
        return False
    ai_ok = (
        label.fundamental_direction in {"positive", "mixed"}
        and label.fundamental_strength >= 2
        and label.expected_horizon not in {"avoid", "unclear"}
        and label.confidence >= 0.5
    )
    if arm == EntryArm.EVENT_PLUS_AI:
        return ai_ok
    if arm == EntryArm.EVENT_PLUS_AI_PLUS_FUNDAMENTAL:
        return ai_ok and fundamental_rule_allows(obs)
    if arm == EntryArm.EVENT_PLUS_AI_PLUS_FUNDAMENTAL_PLUS_TECHNICAL:
        return ai_ok and fundamental_rule_allows(obs) and technical_veto_allows(obs)
    raise ValueError(f"not an AI arm: {arm}")


def feature_bundle_proxy_label(obs: ObservationRecord) -> EventAiLabel:
    """Build a deterministic AI-label proxy from point-in-time feature bundles only."""
    direction = _feature_fundamental_direction(obs)
    strength = _feature_fundamental_strength(obs)
    technical_context = _feature_technical_context(obs)
    return EventAiLabel(
        event_type=obs.event_type,
        fundamental_direction=direction,
        fundamental_strength=strength,
        revision_quality=_feature_revision_quality(obs, strength),
        valuation_context=_feature_valuation_context(obs),
        technical_context=technical_context,
        expected_horizon=_feature_expected_horizon(strength, technical_context),
        risk_flags=_feature_risk_flags(obs),
        confidence=0.8 if direction in {"positive", "mixed"} and strength >= 2 else 0.3,
        rationale="deterministic feature-bundle-only proxy; no LLM, no forward returns",
    )


def fundamental_rule_allows(obs: ObservationRecord) -> bool:
    features = obs.fundamental_features_v0
    profit_pct = features.profit_revision_pct.value
    op_pct = features.operating_profit_revision_pct.value
    eps_abs = features.forecast_eps_revision_absolute.value
    revisions = [_as_decimal(value) for value in (profit_pct, op_pct, eps_abs)]
    if obs.event_type == EventType.DIVIDEND_REVISION:
        return obs.event_subtype == "increase"
    return any(value is not None and value > 0 for value in revisions)


def technical_veto_allows(obs: ObservationRecord) -> bool:
    tech = obs.technical_context_v0
    avg_turnover = _as_decimal(tech.avg_turnover_20d.value)
    atr_pct = _as_decimal(tech.atr_pct_14d.value)
    return_20d = _as_decimal(tech.return_20d.value)
    regime = _technical_regime_value(tech)
    return (
        avg_turnover is not None
        and avg_turnover >= Decimal("200000000")
        and atr_pct is not None
        and Decimal("0.005") <= atr_pct <= Decimal("0.08")
        and (return_20d is None or return_20d < Decimal("0.30"))
        and regime != "broad_downtrend"
    )


def _technical_regime_value(tech: Any) -> str:
    symbol_regime = getattr(tech, "symbol_regime", None)
    if symbol_regime is not None and getattr(symbol_regime, "value", None):
        return str(symbol_regime.value)
    market_regime = getattr(tech, "market_regime", None)
    return str(getattr(market_regime, "value", "") or "")


def _feature_fundamental_direction(obs: ObservationRecord) -> str:
    if obs.event_type == EventType.DIVIDEND_REVISION:
        if obs.event_subtype == "increase":
            return "positive"
        if obs.event_subtype == "decrease":
            return "negative"
        return "unclear"
    values = _revision_values(obs)
    positives = sum(1 for value in values if value is not None and value > 0)
    negatives = sum(1 for value in values if value is not None and value < 0)
    if positives and negatives:
        return "mixed"
    if positives:
        return "positive"
    if negatives:
        return "negative"
    return "neutral"


def _feature_fundamental_strength(obs: ObservationRecord) -> int:
    features = obs.fundamental_features_v0
    if obs.event_type == EventType.DIVIDEND_REVISION:
        return 2 if obs.event_subtype in {"increase", "decrease"} else 0
    values = _revision_values(obs)
    positives = sum(1 for value in values if value is not None and value > 0)
    negatives = sum(1 for value in values if value is not None and value < 0)
    if bool(features.is_loss_to_profit.value):
        return 3
    if positives >= 2:
        return 3
    if positives == 1:
        return 2
    if negatives:
        return 1
    return 0


def _feature_revision_quality(obs: ObservationRecord, strength: int) -> str:
    if obs.event_type == EventType.DIVIDEND_REVISION:
        return "medium" if obs.event_subtype in {"increase", "decrease"} else "unclear"
    if strength >= 3:
        return "high"
    if strength == 2:
        return "medium"
    if strength == 1:
        return "low"
    return "unclear"


def _feature_valuation_context(obs: ObservationRecord) -> str:
    valuation = obs.valuation_features_v0
    forecast_per = _as_decimal(valuation.forecast_per.value)
    if not valuation.forecast_per_valid or forecast_per is None or forecast_per <= 0:
        return "invalid" if valuation.forecast_per.value not in (None, "") else "unclear"
    if forecast_per <= Decimal("15"):
        return "cheap"
    if forecast_per <= Decimal("25"):
        return "fair"
    return "expensive"


def _feature_technical_context(obs: ObservationRecord) -> str:
    if technical_veto_allows(obs):
        return "favorable"
    tech = obs.technical_context_v0
    return_20d = _as_decimal(tech.return_20d.value)
    atr_pct = _as_decimal(tech.atr_pct_14d.value)
    avg_turnover = _as_decimal(tech.avg_turnover_20d.value)
    if return_20d is not None and return_20d >= Decimal("0.30"):
        return "extended"
    low_turnover = avg_turnover is not None and avg_turnover < Decimal("200000000")
    extreme_atr = atr_pct is not None and (atr_pct < Decimal("0.005") or atr_pct > Decimal("0.08"))
    if low_turnover or extreme_atr or _technical_regime_value(tech) == "broad_downtrend":
        return "high_risk"
    return "neutral"


def _feature_expected_horizon(strength: int, technical_context: str) -> str:
    if strength < 2 or technical_context == "high_risk":
        return "avoid"
    if strength >= 3:
        return "20d"
    return "10d"


def _feature_risk_flags(obs: ObservationRecord) -> list[str]:
    flags: list[str] = []
    features = obs.fundamental_features_v0
    tech = obs.technical_context_v0
    if features.missing_eps:
        flags.append("missing_eps")
    if features.sign_changed:
        flags.append("eps_sign_changed")
    if features.previous_eps_near_zero:
        flags.append("previous_eps_near_zero")
    if _technical_regime_value(tech) == "broad_downtrend":
        flags.append("broad_downtrend")
    atr_pct = _as_decimal(tech.atr_pct_14d.value)
    if atr_pct is not None and atr_pct > Decimal("0.08"):
        flags.append("extreme_atr")
    return flags


def _revision_values(obs: ObservationRecord) -> list[Decimal | None]:
    features = obs.fundamental_features_v0
    return [
        _as_decimal(features.profit_revision_pct.value),
        _as_decimal(features.operating_profit_revision_pct.value),
        _as_decimal(features.forecast_eps_revision_absolute.value),
        _as_decimal(features.sales_revision_pct.value),
    ]


def shuffle_labels_within_event_type(
    labels: dict[str, EventAiLabel],
    observations: list[ObservationRecord],
    *,
    seed: int,
) -> dict[str, EventAiLabel]:
    rng = random.Random(seed)
    by_type: dict[EventType, list[EventAiLabel]] = defaultdict(list)
    for obs in observations:
        label = labels.get(obs.event_id)
        if label is not None:
            by_type[obs.event_type].append(label)
    for items in by_type.values():
        rng.shuffle(items)
    cursors: dict[EventType, int] = defaultdict(int)
    out: dict[str, EventAiLabel] = {}
    for obs in observations:
        pool = by_type.get(obs.event_type, [])
        if not pool:
            continue
        idx = cursors[obs.event_type] % len(pool)
        out[obs.event_id] = pool[idx]
        cursors[obs.event_type] += 1
    return out


def shuffle_confidence_within_event_type(
    labels: dict[str, EventAiLabel],
    observations: list[ObservationRecord],
    *,
    seed: int,
) -> dict[str, EventAiLabel]:
    rng = random.Random(seed)
    by_type: dict[EventType, list[float]] = defaultdict(list)
    for obs in observations:
        label = labels.get(obs.event_id)
        if label is not None:
            by_type[obs.event_type].append(label.confidence)
    for items in by_type.values():
        rng.shuffle(items)
    cursors: dict[EventType, int] = defaultdict(int)
    out: dict[str, EventAiLabel] = {}
    for obs in observations:
        label = labels.get(obs.event_id)
        if label is None:
            continue
        pool = by_type.get(obs.event_type, [])
        if not pool:
            continue
        idx = cursors[obs.event_type] % len(pool)
        out[obs.event_id] = label.model_copy(update={"confidence": pool[idx]})
        cursors[obs.event_type] += 1
    return out


def random_threshold_labels_within_event_type(
    labels: dict[str, EventAiLabel],
    observations: list[ObservationRecord],
    *,
    seed: int,
) -> dict[str, EventAiLabel]:
    rng = random.Random(seed)
    pass_counts: dict[EventType, int] = defaultdict(int)
    label_counts: dict[EventType, int] = defaultdict(int)
    for obs in observations:
        label = labels.get(obs.event_id)
        if label is None:
            continue
        label_counts[obs.event_type] += 1
        if ai_arm_allows(obs, label, EntryArm.EVENT_PLUS_AI):
            pass_counts[obs.event_type] += 1
    pass_rates = {
        event_type: pass_counts[event_type] / count for event_type, count in label_counts.items()
    }
    out: dict[str, EventAiLabel] = {}
    for obs in observations:
        label = labels.get(obs.event_id)
        if label is None:
            continue
        if rng.random() < pass_rates.get(obs.event_type, 0.0):
            out[obs.event_id] = label.model_copy(
                update={
                    "fundamental_direction": "positive",
                    "fundamental_strength": 2,
                    "expected_horizon": "10d",
                    "confidence": 0.8,
                }
            )
        else:
            out[obs.event_id] = label.model_copy(update={"confidence": 0.0})
    return out


def _as_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
