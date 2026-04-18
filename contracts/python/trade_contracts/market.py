from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class TickData(BaseModel):
    """個別の約定 Tick。"""

    symbol: str
    timestamp: datetime
    price: Decimal
    volume: int = Field(ge=0)


class PriceLevel(BaseModel):
    price: Decimal
    quantity: int = Field(ge=0)


class OrderBookSnapshot(BaseModel):
    """板情報スナップショット。bids は高値から、asks は安値から。"""

    symbol: str
    timestamp: datetime
    bids: list[PriceLevel]
    asks: list[PriceLevel]
