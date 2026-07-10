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
    """板情報スナップショット。bids は高値から、asks は安値から。

    ``timestamp`` は取引所イベント時刻 (現状は kabu の
    ``CurrentPriceTime``) であり、板の受信時刻ではない。``received_at`` は
    Feeder が live WebSocket メッセージを受け取った時刻で、旧 payload と
    replay データでは ``None`` を許容する。
    """

    symbol: str
    timestamp: datetime
    received_at: datetime | None = None
    bids: list[PriceLevel]
    asks: list[PriceLevel]
