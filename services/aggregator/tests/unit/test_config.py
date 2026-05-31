from __future__ import annotations

from decimal import Decimal

from aggregator.config import AggregatorSettings
from trade_contracts.enums import TradingStyle


def test_defaults() -> None:
    s = AggregatorSettings(_env_file=None)
    assert s.source_weight_rule == Decimal("1.0")
    assert s.source_weight_ai == Decimal("1.0")
    assert s.consensus_min_confidence == Decimal("0.3")
    assert s.min_confidence_rule_only == Decimal("0.5")
    assert s.min_confidence_ai_only == Decimal("0.5")
    assert s.min_confidence_consensus == Decimal("0.3")
    assert s.conflict_policy == "skip"
    assert s.pairing_bucket_ms == 1000
    assert s.pairing_window_ms == 1000
    assert s.default_holding_type is TradingStyle.DAY
    assert s.pubsub_topic_trade_signals == "trade-signals"


def test_threshold_env_overrides(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MIN_CONFIDENCE_RULE_ONLY", "0.55")
    monkeypatch.setenv("MIN_CONFIDENCE_AI_ONLY", "0.45")
    monkeypatch.setenv("MIN_CONFIDENCE_CONSENSUS", "0.35")
    s = AggregatorSettings(_env_file=None)
    assert s.min_confidence_rule_only == Decimal("0.55")
    assert s.min_confidence_ai_only == Decimal("0.45")
    assert s.min_confidence_consensus == Decimal("0.35")
