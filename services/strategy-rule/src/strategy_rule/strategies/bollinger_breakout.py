from __future__ import annotations

from collections.abc import MutableMapping
from decimal import Decimal
from typing import Any

from trade_contracts.enums import Action, SignalSource
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal, execution_fields_from

from .entry_filters import passes_buy_entry_filters

_BUY_ARMED_KEY = "buy_armed"
_BUY_DISTANCE_KEY = "buy_distance"


class BollingerBreakoutStrategy:
    """Mean-reversion trigger when price punches outside the Bollinger band.

    `price < lower - tolerance*band_width` -> BUY.
    `price > upper + tolerance*band_width` -> SELL.

    Confidence is the distance past the band normalised by band width,
    capped at 1.0. `tolerance > 0` requires a stronger breakout to fire.
    """

    name = "bollinger_breakout"

    def __init__(
        self,
        *,
        tolerance: Decimal = Decimal("0"),
        volume_ratio_min: Decimal | None = None,
        require_buy_lower_reclaim: bool = False,
    ) -> None:
        self._tolerance = tolerance
        self._volume_ratio_min = volume_ratio_min
        self._require_buy_lower_reclaim = require_buy_lower_reclaim

    def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None:
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
            distance = (lower - price) / band_width
            if self._require_buy_lower_reclaim:
                state[_BUY_ARMED_KEY] = True
                state[_BUY_DISTANCE_KEY] = distance
                return None
            if not passes_buy_entry_filters(
                features,
                volume_ratio_min=self._volume_ratio_min,
            ):
                return None
            action = Action.BUY
            reasoning = f"price={price} below lower band={lower} (band_width={band_width})"
        elif self._require_buy_lower_reclaim and state.get(_BUY_ARMED_KEY) and price >= lower:
            if not passes_buy_entry_filters(
                features,
                volume_ratio_min=self._volume_ratio_min,
            ):
                return None
            action = Action.BUY
            raw_distance = state.get(_BUY_DISTANCE_KEY, Decimal("0"))
            distance = raw_distance if isinstance(raw_distance, Decimal) else Decimal("0")
            state[_BUY_ARMED_KEY] = False
            state[_BUY_DISTANCE_KEY] = Decimal("0")
            reasoning = (
                f"price={price} reclaimed lower band={lower} "
                f"after lower-band break (band_width={band_width})"
            )
        elif price > upper + margin:
            state[_BUY_ARMED_KEY] = False
            state[_BUY_DISTANCE_KEY] = Decimal("0")
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
            **execution_fields_from(features),
            created_at=features.timestamp,
        )
