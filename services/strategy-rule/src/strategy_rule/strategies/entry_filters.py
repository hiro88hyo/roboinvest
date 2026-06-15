from __future__ import annotations

from decimal import Decimal

from trade_contracts.features import ProcessedFeatures


def passes_buy_entry_filters(
    features: ProcessedFeatures,
    *,
    volume_ratio_min: Decimal | None,
    require_price_above_vwap: bool = False,
    require_sma_uptrend: bool = False,
) -> bool:
    """Return whether a BUY entry can pass optional risk filters."""
    if (
        volume_ratio_min is not None
        and (features.volume_ratio is None or features.volume_ratio < volume_ratio_min)
    ):
        return False
    if require_price_above_vwap and (features.vwap is None or features.price < features.vwap):
        return False
    return not (
        require_sma_uptrend
        and (
            features.sma_short is None
            or features.sma_long is None
            or features.sma_short < features.sma_long
        )
    )
