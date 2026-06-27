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
