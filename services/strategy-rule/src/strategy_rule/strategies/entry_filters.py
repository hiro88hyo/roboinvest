from __future__ import annotations

from decimal import Decimal

from trade_contracts.features import ProcessedFeatures


def passes_buy_entry_filters(
    features: ProcessedFeatures,
    *,
    volume_ratio_min: Decimal | None,
    require_price_above_vwap: bool = False,
    require_sma_uptrend: bool = False,
    max_spread_bps: Decimal | None = None,
    max_spread_ticks: Decimal | None = None,
    min_ask_depth_5: int | None = None,
    min_book_imbalance_5: Decimal | None = None,
    min_minutes_from_open: int | None = None,
    min_minutes_to_close: int | None = None,
) -> bool:
    """Return whether a BUY entry can pass optional risk filters."""
    if volume_ratio_min is not None and (
        features.volume_ratio is None or features.volume_ratio < volume_ratio_min
    ):
        return False
    if require_price_above_vwap and (features.vwap is None or features.price < features.vwap):
        return False
    if require_sma_uptrend and (
        features.sma_short is None
        or features.sma_long is None
        or features.sma_short < features.sma_long
    ):
        return False
    if max_spread_bps is not None and (
        features.spread_bps is None or features.spread_bps > max_spread_bps
    ):
        return False
    if max_spread_ticks is not None and (
        features.spread_ticks is None or features.spread_ticks > max_spread_ticks
    ):
        return False
    if min_ask_depth_5 is not None and (
        features.ask_depth_5 is None or features.ask_depth_5 < min_ask_depth_5
    ):
        return False
    if min_book_imbalance_5 is not None and (
        features.book_imbalance_5 is None or features.book_imbalance_5 < min_book_imbalance_5
    ):
        return False
    if min_minutes_from_open is not None and (
        features.minutes_from_open is None or features.minutes_from_open < min_minutes_from_open
    ):
        return False
    return not (
        min_minutes_to_close is not None
        and (
            features.minutes_to_close is None
            or features.minutes_to_close < min_minutes_to_close
        )
    )


def buy_entry_filter_labels(
    *,
    volume_ratio_min: Decimal | None,
    require_price_above_vwap: bool = False,
    require_sma_uptrend: bool = False,
    max_spread_bps: Decimal | None = None,
    max_spread_ticks: Decimal | None = None,
    min_ask_depth_5: int | None = None,
    min_book_imbalance_5: Decimal | None = None,
    min_minutes_from_open: int | None = None,
    min_minutes_to_close: int | None = None,
) -> list[str]:
    labels: list[str] = []
    if volume_ratio_min is not None:
        labels.append(f"volume_ratio_min={volume_ratio_min}")
    if require_price_above_vwap:
        labels.append("price>=vwap")
    if require_sma_uptrend:
        labels.append("sma_short>=sma_long")
    if max_spread_bps is not None:
        labels.append(f"spread_bps<={max_spread_bps}")
    if max_spread_ticks is not None:
        labels.append(f"spread_ticks<={max_spread_ticks}")
    if min_ask_depth_5 is not None:
        labels.append(f"ask_depth_5>={min_ask_depth_5}")
    if min_book_imbalance_5 is not None:
        labels.append(f"book_imbalance_5>={min_book_imbalance_5}")
    if min_minutes_from_open is not None:
        labels.append(f"minutes_from_open>={min_minutes_from_open}")
    if min_minutes_to_close is not None:
        labels.append(f"minutes_to_close>={min_minutes_to_close}")
    return labels
