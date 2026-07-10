from enum import StrEnum


class Side(StrEnum):
    """注文・約定の方向。"""

    BUY = "BUY"
    SELL = "SELL"


class PositionSide(StrEnum):
    """ポジション方向。現物株のため LONG のみ。"""

    LONG = "LONG"


class Action(StrEnum):
    """シグナルが推奨するアクション。"""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class SignalSource(StrEnum):
    """シグナルの出所。"""

    RULE = "RULE"
    AI = "AI"
    CONSENSUS = "CONSENSUS"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class TradeMode(StrEnum):
    """system_status.trade_mode"""

    LIVE = "live"
    PAPER = "paper"


class RoutingIntent(StrEnum):
    """A signal-level upper bound on where an order may be routed."""

    SYSTEM = "SYSTEM"
    PAPER_ONLY = "PAPER_ONLY"


class TradingStyle(StrEnum):
    """system_status.trading_style / positions.holding_type"""

    DAY = "day"
    SWING = "swing"


class TradeType(StrEnum):
    """positions.trade_type"""

    LIVE = "live"
    PAPER = "paper"
