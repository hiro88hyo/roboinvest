from __future__ import annotations

from collections.abc import MutableMapping
from decimal import Decimal
from typing import Any

from trade_contracts.enums import Action, SignalSource
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal, execution_fields_from

from .entry_filters import passes_buy_entry_filters


class RelativeMomentumStrategy:
    """Long-only intraday relative momentum candidate.

    The strategy expects Feature Engine to provide cross-sectional momentum
    fields. It emits a BUY when a symbol is strong versus its current peer
    universe, above VWAP, and updating the intraday high with acceptable
    execution quality.
    """

    name = "relative_momentum"

    def __init__(
        self,
        *,
        min_return_from_open_bps: Decimal = Decimal("300"),
        min_peer_percentile: Decimal = Decimal("0.9"),
        min_vwap_distance_bps: Decimal = Decimal("30"),
        min_minutes_from_open: int = 15,
        min_minutes_to_close: int = 45,
        max_stop_risk_bps: Decimal | None = Decimal("200"),
        target_r_multiple: Decimal | None = Decimal("1.5"),
        max_price: Decimal | None = None,
        max_spread_bps: Decimal | None = None,
        max_spread_ticks: Decimal | None = None,
        min_ask_depth_5: int | None = None,
        min_book_imbalance_5: Decimal | None = None,
        max_book_age_seconds: Decimal | None = None,
    ) -> None:
        self._min_return_from_open_bps = min_return_from_open_bps
        self._min_peer_percentile = min_peer_percentile
        self._min_vwap_distance_bps = min_vwap_distance_bps
        self._min_minutes_from_open = min_minutes_from_open
        self._min_minutes_to_close = min_minutes_to_close
        self._max_stop_risk_bps = max_stop_risk_bps
        self._target_r_multiple = target_r_multiple
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
        if state.get("traded_today") is True:
            return None
        if not self._passes_filters(features):
            return None
        assert features.vwap is not None
        stop_price = features.vwap
        if stop_price >= features.price:
            return None
        risk_bps = ((features.price - stop_price) / features.price) * Decimal("10000")
        if self._max_stop_risk_bps is not None and risk_bps > self._max_stop_risk_bps:
            return None

        target_price = self._target_price(features.price, stop_price)
        state["traded_today"] = True
        confidence = self._confidence(features=features, risk_bps=risk_bps)
        return StrategySignal(
            source=SignalSource.RULE,
            symbol=features.symbol,
            price=features.price,
            action=Action.BUY,
            confidence=confidence,
            reasoning=(
                "relative_momentum "
                f"return_from_open_bps={features.return_from_open_bps} "
                f"peer_percentile={features.intraday_peer_percentile} "
                f"vwap_distance_bps={self._vwap_distance_bps(features)} "
                f"stop={stop_price} risk_bps={risk_bps:.3f}"
            ),
            stop_loss_price=stop_price,
            target_price=target_price,
            trailing_stop_pct=None,
            max_hold_days=None,
            **execution_fields_from(features),
            created_at=features.timestamp,
        )

    def _passes_filters(self, features: ProcessedFeatures) -> bool:
        if (
            features.return_from_open_bps is None
            or features.return_from_open_bps < self._min_return_from_open_bps
        ):
            return False
        if (
            features.intraday_peer_percentile is None
            or features.intraday_peer_percentile < self._min_peer_percentile
        ):
            return False
        if features.intraday_high_price is None or features.price < features.intraday_high_price:
            return False
        distance_bps = self._vwap_distance_bps(features)
        if distance_bps is None or distance_bps < self._min_vwap_distance_bps:
            return False
        return passes_buy_entry_filters(
            features,
            volume_ratio_min=None,
            max_price=self._max_price,
            require_price_above_vwap=True,
            require_sma_uptrend=False,
            max_spread_bps=self._max_spread_bps,
            max_spread_ticks=self._max_spread_ticks,
            min_ask_depth_5=self._min_ask_depth_5,
            min_book_imbalance_5=self._min_book_imbalance_5,
            min_minutes_from_open=self._min_minutes_from_open,
            min_minutes_to_close=self._min_minutes_to_close,
            max_book_age_seconds=self._max_book_age_seconds,
        )

    def _vwap_distance_bps(self, features: ProcessedFeatures) -> Decimal | None:
        if features.vwap is None or features.vwap <= 0:
            return None
        return ((features.price - features.vwap) / features.vwap) * Decimal("10000")

    def _target_price(self, price: Decimal, stop_price: Decimal) -> Decimal | None:
        if self._target_r_multiple is None:
            return None
        risk = price - stop_price
        if risk <= 0:
            return None
        return price + (risk * self._target_r_multiple)

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

    def _confidence(self, *, features: ProcessedFeatures, risk_bps: Decimal) -> float:
        peer_quality = min(
            Decimal("1"),
            max(Decimal("0"), (features.intraday_peer_percentile or Decimal("0"))),
        )
        momentum_quality = min(
            Decimal("1"),
            (features.return_from_open_bps or Decimal("0"))
            / max(self._min_return_from_open_bps * Decimal("3"), Decimal("1")),
        )
        risk_cap = self._max_stop_risk_bps or Decimal("200")
        risk_quality = Decimal("1") - min(Decimal("1"), risk_bps / risk_cap)
        confidence = (
            Decimal("0.55")
            + (peer_quality * Decimal("0.15"))
            + (momentum_quality * Decimal("0.10"))
            + (risk_quality * Decimal("0.10"))
        )
        return float(min(Decimal("0.9"), max(Decimal("0.55"), confidence)))
