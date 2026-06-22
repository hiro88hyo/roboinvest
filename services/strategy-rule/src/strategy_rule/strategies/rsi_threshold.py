from __future__ import annotations

from collections.abc import MutableMapping
from decimal import Decimal
from typing import Any

from trade_contracts.enums import Action, SignalSource
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal, execution_fields_from

from .entry_filters import buy_entry_filter_labels, passes_buy_entry_filters
from .exit_fields import buy_exit_fields


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
        self._buy = buy_threshold
        self._sell = sell_threshold
        self._volume_ratio_min = volume_ratio_min
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
        del state  # stateless
        rsi = features.rsi
        if rsi is None:
            return None

        if rsi <= self._buy:
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
            confidence = 0.5 + 0.5 * float((self._buy - rsi) / self._buy) if self._buy > 0 else 1.0
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
            filter_suffix = "" if not filters else f" filters=({','.join(filters)})"
            reasoning = f"rsi={rsi} <= buy_threshold={self._buy}{filter_suffix}"
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
            **buy_exit_fields(
                action=action,
                price=features.price,
                target_pct=self._buy_target_pct,
                trailing_stop_pct=self._buy_trailing_stop_pct,
            ),
            **execution_fields_from(features),
            created_at=features.timestamp,
        )
