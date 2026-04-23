from __future__ import annotations

from decimal import Decimal

from aggregator.config import AggregatorSettings
from trade_contracts.enums import TradingStyle


def test_defaults() -> None:
    s = AggregatorSettings(_env_file=None)
    assert s.source_weight_rule == Decimal("1.0")
    assert s.source_weight_ai == Decimal("1.0")
    assert s.consensus_min_confidence == Decimal("0.3")
    assert s.conflict_policy == "skip"
    assert s.pairing_bucket_ms == 1000
    assert s.pairing_window_ms == 1000
    assert s.default_holding_type is TradingStyle.DAY
    assert s.pubsub_topic_trade_signals == "trade-signals"
