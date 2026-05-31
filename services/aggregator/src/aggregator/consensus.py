"""Phase 1 consensus logic: pure function over StrategySignal -> UnifiedTradeSignal.

Given up to two StrategySignals (one per source: RULE / AI) for the same symbol,
return a UnifiedTradeSignal or None according to the rules described in
services/aggregator/CLAUDE.md.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from trade_contracts.enums import Action, SignalSource, TradingStyle
from trade_contracts.signal import StrategySignal, UnifiedTradeSignal

from .config import ConflictPolicy


@dataclass(frozen=True, slots=True)
class ConsensusConfig:
    weight_rule: Decimal = Decimal("1.0")
    weight_ai: Decimal = Decimal("1.0")
    min_confidence: Decimal = Decimal("0.3")
    min_confidence_rule_only: Decimal = Decimal("0.5")
    min_confidence_ai_only: Decimal = Decimal("0.5")
    min_confidence_consensus: Decimal = Decimal("0.3")
    conflict_policy: ConflictPolicy = "skip"
    default_holding_type: TradingStyle = TradingStyle.DAY

    def threshold_for(self, source: SignalSource) -> Decimal:
        if source is SignalSource.RULE:
            return self.min_confidence_rule_only
        if source is SignalSource.AI:
            return self.min_confidence_ai_only
        if source is SignalSource.CONSENSUS:
            return self.min_confidence_consensus
        return self.min_confidence


def _pick_dominant(signals: Iterable[StrategySignal]) -> StrategySignal | None:
    candidates = list(signals)
    if len({s.action for s in candidates}) > 1:
        return None

    best: StrategySignal | None = None
    for s in candidates:
        if best is None or s.confidence > best.confidence:
            best = s
    return best


def _bucket_by_source(
    signals: Iterable[StrategySignal],
) -> tuple[StrategySignal | None, StrategySignal | None]:
    rule_candidates: list[StrategySignal] = []
    ai_candidates: list[StrategySignal] = []
    for s in signals:
        if s.source is SignalSource.RULE:
            rule_candidates.append(s)
        elif s.source is SignalSource.AI:
            ai_candidates.append(s)
        # CONSENSUS inputs are ignored — aggregator produces them, not consumes.
    return _pick_dominant(rule_candidates), _pick_dominant(ai_candidates)


def _newer(a: StrategySignal, b: StrategySignal) -> StrategySignal:
    return a if a.created_at >= b.created_at else b


def _build_unified(
    *,
    symbol: str,
    price: Decimal | None,
    action: Action,
    confidence: Decimal,
    signal_source: SignalSource,
    rule: StrategySignal | None,
    ai: StrategySignal | None,
    holding_type: TradingStyle,
    now: datetime,
) -> UnifiedTradeSignal:
    return UnifiedTradeSignal(
        symbol=symbol,
        price=price,
        action=action,
        confidence=float(confidence),
        signal_source=signal_source,
        strategy_signal_id_a=rule.signal_id if rule is not None else None,
        strategy_signal_id_b=ai.signal_id if ai is not None else None,
        holding_type=holding_type,
        created_at=now,
    )


def aggregate(
    signals: Iterable[StrategySignal],
    *,
    config: ConsensusConfig | None = None,
    now: datetime | None = None,
) -> UnifiedTradeSignal | None:
    """Combine a batch of same-symbol StrategySignals into a UnifiedTradeSignal.

    Returns None when:
      - the input is empty,
      - both inputs conflict and policy is "skip",
      - the resulting confidence is below the threshold for its source.
    """

    cfg = config or ConsensusConfig()
    signals = list(signals)
    if not signals:
        return None

    symbols = {s.symbol for s in signals}
    if len(symbols) != 1:
        raise ValueError(f"aggregate() requires same-symbol signals, got: {symbols}")
    symbol = symbols.pop()

    rule, ai = _bucket_by_source(signals)
    if rule is None and ai is None:
        return None

    stamp = now or datetime.now(UTC)
    latest = _newer(rule, ai) if rule is not None and ai is not None else None

    # Only one side present — pass through with that source.
    if rule is None or ai is None:
        only = rule if rule is not None else ai
        assert only is not None  # narrow for mypy
        confidence = Decimal(str(only.confidence))
        if confidence < cfg.threshold_for(only.source):
            return None
        return _build_unified(
            symbol=symbol,
            price=only.price,
            action=only.action,
            confidence=confidence,
            signal_source=only.source,
            rule=rule,
            ai=ai,
            holding_type=cfg.default_holding_type,
            now=stamp,
        )

    # Both present — check agreement.
    if rule.action == ai.action:
        total_weight = cfg.weight_rule + cfg.weight_ai
        if total_weight <= 0:
            raise ValueError("weight_rule + weight_ai must be positive")
        blended = (
            Decimal(str(rule.confidence)) * cfg.weight_rule
            + Decimal(str(ai.confidence)) * cfg.weight_ai
        ) / total_weight
        if blended < cfg.threshold_for(SignalSource.CONSENSUS):
            return None
        return _build_unified(
            symbol=symbol,
            price=latest.price if latest is not None else None,
            action=rule.action,
            confidence=blended,
            signal_source=SignalSource.CONSENSUS,
            rule=rule,
            ai=ai,
            holding_type=cfg.default_holding_type,
            now=stamp,
        )

    # Conflict — apply policy.
    if cfg.conflict_policy == "skip":
        return None

    winner = rule if cfg.conflict_policy == "prefer_rule" else ai
    confidence = Decimal(str(winner.confidence))
    if confidence < cfg.threshold_for(winner.source):
        return None
    return _build_unified(
        symbol=symbol,
        price=winner.price,
        action=winner.action,
        confidence=confidence,
        signal_source=winner.source,
        rule=rule,
        ai=ai,
        holding_type=cfg.default_holding_type,
        now=_newer(rule, ai).created_at if now is None else stamp,
    )
