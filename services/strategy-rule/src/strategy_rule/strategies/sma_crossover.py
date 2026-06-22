from __future__ import annotations

from collections.abc import MutableMapping
from decimal import Decimal
from typing import Any

from trade_contracts.enums import Action, SignalSource
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal, execution_fields_from

from .entry_filters import buy_entry_filter_labels, passes_buy_entry_filters
from .exit_fields import buy_exit_fields

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
        volume_ratio_min: Decimal | None = None,
        require_price_above_vwap: bool = False,
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
        self._min_gap_ratio = min_gap_ratio
        self._full_confidence_gap_ratio = full_confidence_gap_ratio
        self._volume_ratio_min = volume_ratio_min
        self._require_price_above_vwap = require_price_above_vwap
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
            if not passes_buy_entry_filters(
                features,
                volume_ratio_min=self._volume_ratio_min,
                max_price=self._max_price,
                require_price_above_vwap=self._require_price_above_vwap,
                require_sma_uptrend=False,
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
        elif prev_diff >= 0 and diff < 0:
            action = Action.SELL
        else:
            return None

        if self._full_confidence_gap_ratio > 0:
            confidence = float(gap_ratio / self._full_confidence_gap_ratio)
        else:
            confidence = 1.0
        confidence = min(1.0, max(0.0, confidence))
        filter_suffix = ""
        if action is Action.BUY:
            filters = buy_entry_filter_labels(
                volume_ratio_min=self._volume_ratio_min,
                max_price=self._max_price,
                require_price_above_vwap=self._require_price_above_vwap,
                require_sma_uptrend=False,
                max_spread_bps=self._max_spread_bps,
                max_spread_ticks=self._max_spread_ticks,
                min_ask_depth_5=self._min_ask_depth_5,
                min_book_imbalance_5=self._min_book_imbalance_5,
                min_minutes_from_open=self._min_minutes_from_open,
                min_minutes_to_close=self._min_minutes_to_close,
                max_book_age_seconds=self._max_book_age_seconds,
            )
            if filters:
                filter_suffix = f" filters=({','.join(filters)})"

        return StrategySignal(
            source=SignalSource.RULE,
            symbol=features.symbol,
            price=features.price,
            action=action,
            confidence=confidence,
            reasoning=(
                f"sma_crossover: short-long diff {prev_diff} -> {diff} "
                f"(gap_ratio={gap_ratio:.5f}){filter_suffix}"
            ),
            **buy_exit_fields(
                action=action,
                price=features.price,
                target_pct=self._buy_target_pct,
                trailing_stop_pct=self._buy_trailing_stop_pct,
            ),
            **execution_fields_from(features),
            created_at=features.timestamp,
        )
