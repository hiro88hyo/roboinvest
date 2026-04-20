from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal

from ..engine import StrategyEngine

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class BacktestSummary:
    feature_count: int
    signal_count: int
    signals: list[StrategySignal]


def run_backtest(
    engine: StrategyEngine,
    features: Iterable[ProcessedFeatures],
) -> BacktestSummary:
    """Drive `engine` over `features` and collect every signal in order.

    Order is preserved: signals from the same feature appear in strategy
    declaration order, and features are processed in input order so each
    strategy's per-symbol state evolves naturally over the series.
    """
    feature_count = 0
    out: list[StrategySignal] = []
    for feature in features:
        feature_count += 1
        out.extend(engine.evaluate(feature))
    logger.info(
        "backtest done: features=%d signals=%d strategies=%d",
        feature_count,
        len(out),
        len(engine.strategies),
    )
    return BacktestSummary(feature_count=feature_count, signal_count=len(out), signals=out)
