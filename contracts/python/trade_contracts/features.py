from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from .market import OrderBookSnapshot


class ProcessedFeatures(BaseModel):
    """Feature Engine が算出するテクニカル指標の統合ビュー。"""

    symbol: str
    timestamp: datetime
    price: Decimal

    sma_short: Decimal | None = None
    sma_long: Decimal | None = None
    rsi: Decimal | None = None
    vwap: Decimal | None = None
    volume_ratio: Decimal | None = None
    bollinger_upper: Decimal | None = None
    bollinger_middle: Decimal | None = None
    bollinger_lower: Decimal | None = None

    order_book: OrderBookSnapshot | None = None
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    spread_bps: Decimal | None = None
    tick_size: Decimal | None = None
    spread_ticks: Decimal | None = None
    bid_depth_1: int | None = None
    ask_depth_1: int | None = None
    bid_depth_5: int | None = None
    ask_depth_5: int | None = None
    book_imbalance_5: Decimal | None = None
    minutes_from_open: int | None = None
    minutes_to_close: int | None = None
    session_phase: str | None = None
