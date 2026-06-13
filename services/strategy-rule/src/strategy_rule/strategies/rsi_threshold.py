from __future__ import annotations

from collections.abc import MutableMapping
from decimal import Decimal
from typing import Any

from trade_contracts.enums import Action, SignalSource
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal

from .entry_filters import passes_buy_entry_filters


class RsiThresholdStrategy:
    """Stateless RSI band trigger.

    `rsi <= buy_threshold` -> BUY (oversold reversion).
    `rsi >= sell_threshold` -> SELL (overbought reversion).

    Confidence ramps linearly from 0.5 at the threshold to 1.0 at the
    extreme (0 for buy, 100 for sell), so a barely-triggered signal is
    distinguishable from a deep extreme.
    """

    name = "rsi_threshold"

    def __init__(
        self,
        *,
        buy_threshold: Decimal = Decimal("30"),
        sell_threshold: Decimal = Decimal("70"),
        volume_ratio_min: Decimal | None = None,
    ) -> None:
        self._buy = buy_threshold
        self._sell = sell_threshold
        self._volume_ratio_min = volume_ratio_min

    def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None:
        del state  # stateless
        rsi = features.rsi
        if rsi is None:
            return None

        if rsi <= self._buy:
            if not passes_buy_entry_filters(
                features,
                volume_ratio_min=self._volume_ratio_min,
            ):
                return None
            action = Action.BUY
            confidence = 0.5 + 0.5 * float((self._buy - rsi) / self._buy) if self._buy > 0 else 1.0
            reasoning = f"rsi={rsi} <= buy_threshold={self._buy}"
        elif rsi >= self._sell:
            action = Action.SELL
            denom = Decimal("100") - self._sell
            confidence = 0.5 + 0.5 * float((rsi - self._sell) / denom) if denom > 0 else 1.0
            reasoning = f"rsi={rsi} >= sell_threshold={self._sell}"
        else:
            return None

        confidence = min(1.0, max(0.0, confidence))
        return StrategySignal(
            source=SignalSource.RULE,
            symbol=features.symbol,
            price=features.price,
            action=action,
            confidence=confidence,
            reasoning=reasoning,
            created_at=features.timestamp,
        )
