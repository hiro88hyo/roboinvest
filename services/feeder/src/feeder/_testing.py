"""ユニットテスト用フィクスチャ。

`conftest.py` は他サービスとの mypy 重複モジュール検出を避けるため置かない。
"""

from __future__ import annotations

from typing import Any


def make_tick_payload(
    *,
    symbol: str = "7203",
    current_price: float = 1000.0,
    current_price_time: str = "2026-04-25T09:00:00+09:00",
    trading_volume: int = 100,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "Symbol": symbol,
        "CurrentPrice": current_price,
        "CurrentPriceTime": current_price_time,
        "TradingVolume": trading_volume,
    }
    if extra:
        payload.update(extra)
    return payload


def make_book_payload(
    *,
    symbol: str = "7203",
    current_price_time: str = "2026-04-25T09:00:00+09:00",
    bids: list[tuple[float, int]] | None = None,
    asks: list[tuple[float, int]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bids_list = bids if bids is not None else [(999.0, 200), (998.0, 500)]
    asks_list = asks if asks is not None else [(1000.0, 200), (1001.0, 500)]
    payload: dict[str, Any] = {
        "Symbol": symbol,
        "CurrentPriceTime": current_price_time,
    }
    for i, (price, qty) in enumerate(bids_list, start=1):
        payload[f"Buy{i}"] = {"Price": price, "Qty": qty}  # kabu: Buy1-Buy10
    for i, (price, qty) in enumerate(asks_list, start=1):
        payload[f"Sell{i}"] = {"Price": price, "Qty": qty}  # kabu: Sell1-Sell10
    if extra:
        payload.update(extra)
    return payload
