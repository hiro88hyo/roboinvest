from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal

from ..engine import StrategyAiEngine

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class BacktestSummary:
    feature_count: int
    signal_count: int
    signals: list[StrategySignal]


async def run_backtest(
    engine: StrategyAiEngine,
    features: Iterable[ProcessedFeatures],
) -> BacktestSummary:
    """Drive `engine` over `features` and collect every signal in order.

    Async 版。strategy-rule.run_backtest と同じ契約だが、AiStrategy が
    `await` するため runner ごと async にする。順序は入力順を保つので、
    レート制御・state の遷移はテストで再現可能。
    """
    feature_count = 0
    out: list[StrategySignal] = []
    for feature in features:
        feature_count += 1
        out.extend(await engine.evaluate(feature))
    logger.info(
        "backtest done: features=%d signals=%d strategies=%d",
        feature_count,
        len(out),
        len(engine.strategies),
    )
    return BacktestSummary(feature_count=feature_count, signal_count=len(out), signals=out)
