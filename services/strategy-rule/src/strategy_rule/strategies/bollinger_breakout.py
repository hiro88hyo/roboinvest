from __future__ import annotations

from collections.abc import MutableMapping
from decimal import Decimal
from typing import Any

from trade_contracts.enums import Action, SignalSource
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal


class BollingerBreakoutStrategy:
    """Mean-reversion trigger when price punches outside the Bollinger band.

    `price < lower - tolerance*band_width` -> BUY.
    `price > upper + tolerance*band_width` -> SELL.

    Confidence is the distance past the band normalised by band width,
    capped at 1.0. `tolerance > 0` requires a stronger breakout to fire.
    """

    name = "bollinger_breakout"

    def __init__(self, *, tolerance: Decimal = Decimal("0")) -> None:
        self._tolerance = tolerance

    def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None:
        del state  # stateless
        upper = features.bollinger_upper
        lower = features.bollinger_lower
        if upper is None or lower is None:
            return None

        band_width = upper - lower
        if band_width <= 0:
            return None

        margin = band_width * self._tolerance
        price = features.price

        if price < lower - margin:
            action = Action.BUY
            distance = (lower - price) / band_width
            reasoning = f"price={price} below lower band={lower} (band_width={band_width})"
        elif price > upper + margin:
            action = Action.SELL
            distance = (price - upper) / band_width
            reasoning = f"price={price} above upper band={upper} (band_width={band_width})"
        else:
            return None

        confidence = min(1.0, max(0.0, float(distance)))
        return StrategySignal(
            source=SignalSource.RULE,
            symbol=features.symbol,
            price=features.price,
            action=action,
            confidence=confidence,
            reasoning=reasoning,
            created_at=features.timestamp,
        )
