from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from aggregator.consensus import ConsensusConfig, aggregate
from trade_contracts.enums import Action, SignalSource, TradingStyle


def _cfg(**overrides: object) -> ConsensusConfig:
    base = dict(
        weight_rule=Decimal("1.0"),
        weight_ai=Decimal("1.0"),
        min_confidence=Decimal("0.3"),
        conflict_policy="skip",
        default_holding_type=TradingStyle.DAY,
    )
    base.update(overrides)
    return ConsensusConfig(**base)  # type: ignore[arg-type]


def test_empty_input_returns_none() -> None:
    assert aggregate([]) is None


def test_mismatched_symbols_raises(signal_factory) -> None:  # type: ignore[no-untyped-def]
    a = signal_factory(source=SignalSource.RULE, symbol="7203")
    b = signal_factory(source=SignalSource.AI, symbol="9984")
    with pytest.raises(ValueError):
        aggregate([a, b])


def test_rule_only_passthrough(signal_factory) -> None:  # type: ignore[no-untyped-def]
    rule = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.8)
    unified = aggregate([rule], config=_cfg())
    assert unified is not None
    assert unified.signal_source is SignalSource.RULE
    assert unified.action is Action.BUY
    assert unified.confidence == pytest.approx(0.8)
    assert unified.strategy_signal_id_a == rule.signal_id
    assert unified.strategy_signal_id_b is None
    assert unified.holding_type is TradingStyle.DAY
    assert unified.stop_loss_price is None
    assert unified.target_price is None


def test_ai_only_passthrough(signal_factory) -> None:  # type: ignore[no-untyped-def]
    ai = signal_factory(source=SignalSource.AI, action=Action.SELL, confidence=0.55)
    unified = aggregate([ai], config=_cfg())
    assert unified is not None
    assert unified.signal_source is SignalSource.AI
    assert unified.action is Action.SELL
    assert unified.confidence == pytest.approx(0.55)
    assert unified.strategy_signal_id_a is None
    assert unified.strategy_signal_id_b == ai.signal_id


def test_agreement_weighted_confidence(signal_factory) -> None:  # type: ignore[no-untyped-def]
    rule = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.8)
    ai = signal_factory(source=SignalSource.AI, action=Action.BUY, confidence=0.4)
    unified = aggregate(
        [rule, ai],
        config=_cfg(weight_rule=Decimal("3.0"), weight_ai=Decimal("1.0")),
    )
    assert unified is not None
    assert unified.signal_source is SignalSource.CONSENSUS
    assert unified.action is Action.BUY
    # (0.8*3 + 0.4*1) / 4 = 0.7
    assert unified.confidence == pytest.approx(0.7)
    assert unified.strategy_signal_id_a == rule.signal_id
    assert unified.strategy_signal_id_b == ai.signal_id


def test_conflict_skip_returns_none(signal_factory) -> None:  # type: ignore[no-untyped-def]
    rule = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.9)
    ai = signal_factory(source=SignalSource.AI, action=Action.SELL, confidence=0.9)
    assert aggregate([rule, ai], config=_cfg(conflict_policy="skip")) is None


def test_conflict_prefer_rule(signal_factory) -> None:  # type: ignore[no-untyped-def]
    rule = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.7)
    ai = signal_factory(source=SignalSource.AI, action=Action.SELL, confidence=0.95)
    unified = aggregate([rule, ai], config=_cfg(conflict_policy="prefer_rule"))
    assert unified is not None
    assert unified.signal_source is SignalSource.RULE
    assert unified.action is Action.BUY
    assert unified.confidence == pytest.approx(0.7)


def test_conflict_prefer_ai(signal_factory) -> None:  # type: ignore[no-untyped-def]
    rule = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.9)
    ai = signal_factory(source=SignalSource.AI, action=Action.SELL, confidence=0.6)
    unified = aggregate([rule, ai], config=_cfg(conflict_policy="prefer_ai"))
    assert unified is not None
    assert unified.signal_source is SignalSource.AI
    assert unified.action is Action.SELL
    assert unified.confidence == pytest.approx(0.6)


def test_min_confidence_drops_single_source(signal_factory) -> None:  # type: ignore[no-untyped-def]
    rule = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.2)
    assert aggregate([rule], config=_cfg(min_confidence=Decimal("0.3"))) is None


def test_min_confidence_drops_consensus(signal_factory) -> None:  # type: ignore[no-untyped-def]
    rule = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.2)
    ai = signal_factory(source=SignalSource.AI, action=Action.BUY, confidence=0.2)
    assert aggregate([rule, ai], config=_cfg(min_confidence=Decimal("0.3"))) is None


def test_min_confidence_drops_conflict_winner(signal_factory) -> None:  # type: ignore[no-untyped-def]
    rule = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.1)
    ai = signal_factory(source=SignalSource.AI, action=Action.SELL, confidence=0.9)
    assert (
        aggregate(
            [rule, ai],
            config=_cfg(conflict_policy="prefer_rule", min_confidence=Decimal("0.3")),
        )
        is None
    )


def test_same_source_picks_highest_confidence(signal_factory) -> None:  # type: ignore[no-untyped-def]
    low = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.4)
    high = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.75)
    unified = aggregate([low, high], config=_cfg())
    assert unified is not None
    assert unified.strategy_signal_id_a == high.signal_id
    assert unified.confidence == pytest.approx(0.75)


def test_same_rule_source_conflict_returns_none(signal_factory) -> None:  # type: ignore[no-untyped-def]
    buy = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.8)
    sell = signal_factory(source=SignalSource.RULE, action=Action.SELL, confidence=0.9)
    assert aggregate([buy, sell], config=_cfg()) is None


def test_same_rule_source_conflict_allows_ai_only(signal_factory) -> None:  # type: ignore[no-untyped-def]
    rule_buy = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.8)
    rule_sell = signal_factory(source=SignalSource.RULE, action=Action.SELL, confidence=0.9)
    ai = signal_factory(source=SignalSource.AI, action=Action.BUY, confidence=0.7)
    unified = aggregate([rule_buy, rule_sell, ai], config=_cfg())
    assert unified is not None
    assert unified.signal_source is SignalSource.AI
    assert unified.action is Action.BUY
    assert unified.strategy_signal_id_a is None
    assert unified.strategy_signal_id_b == ai.signal_id


def test_holding_type_follows_config(signal_factory) -> None:  # type: ignore[no-untyped-def]
    rule = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.8)
    unified = aggregate([rule], config=_cfg(default_holding_type=TradingStyle.SWING))
    assert unified is not None
    assert unified.holding_type is TradingStyle.SWING


def test_unified_signal_id_is_fresh(signal_factory) -> None:  # type: ignore[no-untyped-def]
    rule = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.8)
    unified = aggregate([rule], config=_cfg())
    assert unified is not None
    assert unified.signal_id != rule.signal_id


def test_zero_total_weight_raises(signal_factory) -> None:  # type: ignore[no-untyped-def]
    rule = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.8)
    ai = signal_factory(source=SignalSource.AI, action=Action.BUY, confidence=0.8)
    with pytest.raises(ValueError):
        aggregate(
            [rule, ai],
            config=_cfg(weight_rule=Decimal("0"), weight_ai=Decimal("0")),
        )


def test_consensus_ignores_existing_consensus_inputs(signal_factory) -> None:  # type: ignore[no-untyped-def]
    # Aggregator must not consume its own output.
    rule = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.8)
    stray = signal_factory(source=SignalSource.CONSENSUS, action=Action.SELL, confidence=0.9)
    unified = aggregate([rule, stray], config=_cfg())
    assert unified is not None
    assert unified.signal_source is SignalSource.RULE
    assert unified.action is Action.BUY


def test_now_override_sets_created_at(signal_factory) -> None:  # type: ignore[no-untyped-def]
    rule = signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.8)
    fixed = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
    unified = aggregate([rule], config=_cfg(), now=fixed)
    assert unified is not None
    assert unified.created_at == fixed
