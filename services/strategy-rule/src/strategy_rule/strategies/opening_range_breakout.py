from __future__ import annotations

from collections.abc import MutableMapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from trade_contracts.enums import Action, SignalSource
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal, execution_fields_from

from .entry_filters import passes_buy_entry_filters


class OpeningRangeBreakoutStrategy:
    """Long-only opening range breakout candidate.

    The strategy forms a per-symbol opening range from early-session features,
    then emits one BUY when price crosses above that range while VWAP and
    execution filters pass. It is intended for paper/backtest-first evaluation.
    """

    name = "opening_range_breakout"

    def __init__(
        self,
        *,
        range_minutes: int = 15,
        entry_minute: int = 15,
        min_minutes_to_close: int = 45,
        max_stop_risk_bps: Decimal | None = Decimal("300"),
        cooldown_seconds: int = 900,
        require_vwap: bool = True,
        target_r_multiple: Decimal | None = Decimal("1.5"),
        min_breakout_volume_delta: int | None = None,
        min_opening_range_volume: int | None = None,
        max_price: Decimal | None = None,
        max_spread_bps: Decimal | None = None,
        max_spread_ticks: Decimal | None = None,
        min_ask_depth_5: int | None = None,
        min_book_imbalance_5: Decimal | None = None,
        max_book_age_seconds: Decimal | None = None,
    ) -> None:
        self._range_minutes = range_minutes
        self._entry_minute = entry_minute
        self._min_minutes_to_close = min_minutes_to_close
        self._max_stop_risk_bps = max_stop_risk_bps
        self._cooldown_seconds = cooldown_seconds
        self._require_vwap = require_vwap
        self._target_r_multiple = target_r_multiple
        self._min_breakout_volume_delta = min_breakout_volume_delta
        self._min_opening_range_volume = min_opening_range_volume
        self._max_price = max_price
        self._max_spread_bps = max_spread_bps
        self._max_spread_ticks = max_spread_ticks
        self._min_ask_depth_5 = min_ask_depth_5
        self._min_book_imbalance_5 = min_book_imbalance_5
        self._max_book_age_seconds = max_book_age_seconds

    def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None:
        self._reset_for_new_date(features, state)
        if not self._has_session_time(features):
            state["previous_price"] = features.price
            return None

        if (
            features.minutes_from_open is not None
            and features.minutes_from_open < self._range_minutes
        ):
            self._record_opening_range(features, state)
            state["previous_price"] = features.price
            return None

        signal = self._maybe_breakout(features, state)
        state["previous_price"] = features.price
        return signal

    def _maybe_breakout(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None:
        opening_high = state.get("opening_high")
        opening_low = state.get("opening_low")
        previous_price = state.get("previous_price")
        if not isinstance(opening_high, Decimal) or not isinstance(opening_low, Decimal):
            return None
        if not isinstance(previous_price, Decimal):
            return None
        if state.get("traded_today") is True:
            return None
        if previous_price > opening_high or features.price <= opening_high:
            return None
        if not self._passes_filters(features, state):
            return None
        stop_price = self._stop_price(features=features, opening_low=opening_low)
        if stop_price >= features.price:
            return None
        risk_bps = ((features.price - stop_price) / features.price) * Decimal("10000")
        if self._max_stop_risk_bps is not None and risk_bps > self._max_stop_risk_bps:
            return None
        if self._is_in_cooldown(features.timestamp, state):
            return None

        target_price = self._target_price(features.price, stop_price)
        state["last_signal_at"] = features.timestamp
        state["traded_today"] = True
        confidence = self._confidence(features.price, opening_high, risk_bps)
        return StrategySignal(
            source=SignalSource.RULE,
            symbol=features.symbol,
            price=features.price,
            action=Action.BUY,
            confidence=confidence,
            reasoning=(
                "opening_range_breakout "
                f"price={features.price} opening_high={opening_high} "
                f"opening_low={opening_low} stop={stop_price} risk_bps={risk_bps:.3f}"
            ),
            stop_loss_price=stop_price,
            target_price=target_price,
            trailing_stop_pct=None,
            max_hold_days=None,
            **execution_fields_from(features),
            created_at=features.timestamp,
        )

    def _passes_filters(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> bool:
        if not passes_buy_entry_filters(
            features,
            volume_ratio_min=None,
            max_price=self._max_price,
            require_price_above_vwap=self._require_vwap,
            require_sma_uptrend=False,
            max_spread_bps=self._max_spread_bps,
            max_spread_ticks=self._max_spread_ticks,
            min_ask_depth_5=self._min_ask_depth_5,
            min_book_imbalance_5=self._min_book_imbalance_5,
            min_minutes_from_open=self._entry_minute,
            min_minutes_to_close=self._min_minutes_to_close,
            max_book_age_seconds=self._max_book_age_seconds,
        ):
            return False
        if self._min_breakout_volume_delta is not None and (
            features.trade_volume_delta is None
            or features.trade_volume_delta < self._min_breakout_volume_delta
        ):
            return False
        opening_volume = state.get("opening_volume")
        return not (
            self._min_opening_range_volume is not None
            and (
                not isinstance(opening_volume, int)
                or opening_volume < self._min_opening_range_volume
            )
        )

    def _record_opening_range(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> None:
        high = state.get("opening_high")
        low = state.get("opening_low")
        state["opening_high"] = (
            features.price if not isinstance(high, Decimal) else max(high, features.price)
        )
        state["opening_low"] = (
            features.price if not isinstance(low, Decimal) else min(low, features.price)
        )
        if features.trade_volume_delta is not None:
            opening_volume = state.get("opening_volume", 0)
            if isinstance(opening_volume, int):
                state["opening_volume"] = opening_volume + max(0, features.trade_volume_delta)

    def _reset_for_new_date(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> None:
        current_date = features.timestamp.date()
        if state.get("trading_date") == current_date:
            return
        state.clear()
        state["trading_date"] = current_date

    def _has_session_time(self, features: ProcessedFeatures) -> bool:
        return features.minutes_from_open is not None and features.minutes_to_close is not None

    def _stop_price(self, *, features: ProcessedFeatures, opening_low: Decimal) -> Decimal:
        if features.vwap is None:
            return opening_low
        return max(opening_low, features.vwap)

    def _target_price(self, price: Decimal, stop_price: Decimal) -> Decimal | None:
        if self._target_r_multiple is None:
            return None
        risk = price - stop_price
        if risk <= 0:
            return None
        return price + (risk * self._target_r_multiple)

    def _is_in_cooldown(
        self,
        timestamp: datetime,
        state: MutableMapping[str, Any],
    ) -> bool:
        last = state.get("last_signal_at")
        if not isinstance(last, datetime):
            return False
        return (timestamp - last).total_seconds() < self._cooldown_seconds

    def _confidence(self, price: Decimal, opening_high: Decimal, risk_bps: Decimal) -> float:
        breakout_bps = ((price - opening_high) / price) * Decimal("10000")
        risk_cap = self._max_stop_risk_bps or Decimal("300")
        risk_quality = Decimal("1") - min(Decimal("1"), risk_bps / risk_cap)
        breakout_quality = min(Decimal("1"), breakout_bps / Decimal("50"))
        confidence = (
            Decimal("0.55")
            + (breakout_quality * Decimal("0.20"))
            + (risk_quality * Decimal("0.15"))
        )
        return float(min(Decimal("0.9"), max(Decimal("0.55"), confidence)))
