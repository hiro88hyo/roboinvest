from __future__ import annotations

from collections.abc import MutableMapping
from decimal import Decimal
from typing import Any

from trade_contracts.enums import Action, SignalSource
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal

_PREV_DIFF_KEY = "prev_diff"


class SmaCrossoverStrategy:
    """Short SMA crossing the long SMA.

    Upward cross (short went from <=long to >long) -> BUY.
    Downward cross (short went from >=long to <long) -> SELL.

    Confidence is the absolute gap relative to price, normalised so a gap
    of `full_confidence_gap_ratio` (e.g. 2%) yields 1.0. Setting
    `min_gap_ratio` filters out micro-crosses that are likely just noise.
    """

    name = "sma_crossover"

    def __init__(
        self,
        *,
        min_gap_ratio: Decimal = Decimal("0"),
        full_confidence_gap_ratio: Decimal = Decimal("0.02"),
    ) -> None:
        self._min_gap_ratio = min_gap_ratio
        self._full_confidence_gap_ratio = full_confidence_gap_ratio

    def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None:
        if features.sma_short is None or features.sma_long is None:
            return None

        diff = features.sma_short - features.sma_long
        prev_diff = state.get(_PREV_DIFF_KEY)
        state[_PREV_DIFF_KEY] = diff

        if prev_diff is None:
            return None

        if features.price <= 0:
            return None
        gap_ratio = abs(diff) / features.price
        if gap_ratio < self._min_gap_ratio:
            return None

        if prev_diff <= 0 and diff > 0:
            action = Action.BUY
        elif prev_diff >= 0 and diff < 0:
            action = Action.SELL
        else:
            return None

        if self._full_confidence_gap_ratio > 0:
            confidence = float(gap_ratio / self._full_confidence_gap_ratio)
        else:
            confidence = 1.0
        confidence = min(1.0, max(0.0, confidence))

        return StrategySignal(
            source=SignalSource.RULE,
            symbol=features.symbol,
            price=features.price,
            action=action,
            confidence=confidence,
            reasoning=(
                f"sma_crossover: short-long diff {prev_diff} -> {diff} (gap_ratio={gap_ratio:.5f})"
            ),
            created_at=features.timestamp,
        )
