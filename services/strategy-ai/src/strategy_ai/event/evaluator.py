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
    return any(value is not None and value > 0 for value in revisions) or (
        obs.event_type == EventType.DIVIDEND_REVISION
    )


def technical_veto_allows(obs: ObservationRecord) -> bool:
    tech = obs.technical_context_v0
    avg_turnover = _as_decimal(tech.avg_turnover_20d.value)
    atr_pct = _as_decimal(tech.atr_pct_14d.value)
    return_20d = _as_decimal(tech.return_20d.value)
    regime = str(tech.market_regime.value or "")
    return (
        avg_turnover is not None
        and avg_turnover >= Decimal("200000000")
        and atr_pct is not None
        and Decimal("0.005") <= atr_pct <= Decimal("0.08")
        and (return_20d is None or return_20d < Decimal("0.30"))
        and regime != "broad_downtrend"
    )


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


def _as_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
