from __future__ import annotations

from decimal import Decimal

from trade_contracts.features import ProcessedFeatures


def passes_buy_entry_filters(
    features: ProcessedFeatures,
    *,
    volume_ratio_min: Decimal | None,
) -> bool:
    """Return whether a BUY entry can pass optional risk filters."""
    return not (
        volume_ratio_min is not None
        and (features.volume_ratio is None or features.volume_ratio < volume_ratio_min)
    )
