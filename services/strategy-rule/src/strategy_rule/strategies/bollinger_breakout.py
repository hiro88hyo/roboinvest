from __future__ import annotations

from collections.abc import MutableMapping
from decimal import Decimal
from typing import Any

from trade_contracts.enums import Action, SignalSource
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal, execution_fields_from

from .entry_filters import buy_entry_filter_labels, passes_buy_entry_filters
from .exit_fields import buy_exit_fields

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
        require_price_above_vwap: bool = False,
        require_sma_uptrend: bool = False,
        max_price: Decimal | None = None,
        max_spread_bps: Decimal | None = None,
        max_spread_ticks: Decimal | None = None,
        min_ask_depth_5: int | None = None,
        min_book_imbalance_5: Decimal | None = None,
        min_minutes_from_open: int | None = None,
        min_minutes_to_close: int | None = None,
        max_book_age_seconds: Decimal | None = None,
        buy_target_pct: Decimal | None = None,
        buy_trailing_stop_pct: Decimal | None = None,
    ) -> None:
        self._tolerance = tolerance
        self._volume_ratio_min = volume_ratio_min
        self._require_buy_lower_reclaim = require_buy_lower_reclaim
        self._require_price_above_vwap = require_price_above_vwap
        self._require_sma_uptrend = require_sma_uptrend
        self._max_price = max_price
        self._max_spread_bps = max_spread_bps
        self._max_spread_ticks = max_spread_ticks
        self._min_ask_depth_5 = min_ask_depth_5
        self._min_book_imbalance_5 = min_book_imbalance_5
        self._min_minutes_from_open = min_minutes_from_open
        self._min_minutes_to_close = min_minutes_to_close
        self._max_book_age_seconds = max_book_age_seconds
        self._buy_target_pct = buy_target_pct
        self._buy_trailing_stop_pct = buy_trailing_stop_pct

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
                max_price=self._max_price,
                require_price_above_vwap=self._require_price_above_vwap,
                require_sma_uptrend=self._require_sma_uptrend,
                max_spread_bps=self._max_spread_bps,
                max_spread_ticks=self._max_spread_ticks,
                min_ask_depth_5=self._min_ask_depth_5,
                min_book_imbalance_5=self._min_book_imbalance_5,
                min_minutes_from_open=self._min_minutes_from_open,
                min_minutes_to_close=self._min_minutes_to_close,
                max_book_age_seconds=self._max_book_age_seconds,
            ):
                return None
            action = Action.BUY
            reasoning = f"price={price} below lower band={lower} (band_width={band_width})"
        elif self._require_buy_lower_reclaim and state.get(_BUY_ARMED_KEY) and price >= lower:
            if not passes_buy_entry_filters(
                features,
                volume_ratio_min=self._volume_ratio_min,
                max_price=self._max_price,
                require_price_above_vwap=self._require_price_above_vwap,
                require_sma_uptrend=self._require_sma_uptrend,
                max_spread_bps=self._max_spread_bps,
                max_spread_ticks=self._max_spread_ticks,
                min_ask_depth_5=self._min_ask_depth_5,
                min_book_imbalance_5=self._min_book_imbalance_5,
                min_minutes_from_open=self._min_minutes_from_open,
                min_minutes_to_close=self._min_minutes_to_close,
                max_book_age_seconds=self._max_book_age_seconds,
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
        if action is Action.BUY:
            filters = buy_entry_filter_labels(
                volume_ratio_min=self._volume_ratio_min,
                max_price=self._max_price,
                require_price_above_vwap=self._require_price_above_vwap,
                require_sma_uptrend=self._require_sma_uptrend,
                max_spread_bps=self._max_spread_bps,
                max_spread_ticks=self._max_spread_ticks,
                min_ask_depth_5=self._min_ask_depth_5,
                min_book_imbalance_5=self._min_book_imbalance_5,
                min_minutes_from_open=self._min_minutes_from_open,
                min_minutes_to_close=self._min_minutes_to_close,
                max_book_age_seconds=self._max_book_age_seconds,
            )
            if filters:
                reasoning = f"{reasoning} filters=({','.join(filters)})"
        return StrategySignal(
            source=SignalSource.RULE,
            symbol=features.symbol,
            price=features.price,
            action=action,
            confidence=confidence,
            reasoning=reasoning,
            **buy_exit_fields(
                action=action,
                price=features.price,
                target_pct=self._buy_target_pct,
                trailing_stop_pct=self._buy_trailing_stop_pct,
            ),
            **execution_fields_from(features),
            created_at=features.timestamp,
        )
