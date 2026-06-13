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
