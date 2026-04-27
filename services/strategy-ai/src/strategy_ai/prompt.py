from __future__ import annotations

from decimal import Decimal

from trade_contracts.features import ProcessedFeatures
from trade_contracts.market import OrderBookSnapshot

_INSTRUCTION = (
    "You are a Japanese cash equity trading assistant. "
    "Decide whether to BUY, SELL, or HOLD the given symbol on the next tick "
    "based on the technical indicators and order book snapshot. "
    "Reply with a single JSON object and nothing else, in the form: "
    '{"action": "BUY"|"SELL"|"HOLD", "confidence": 0.0-1.0, '
    '"reasoning": "<short Japanese explanation>"}. '
    "Use HOLD when there is no clear edge."
)


def _fmt(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:f}"


def _fmt_book(book: OrderBookSnapshot | None) -> str:
    if book is None:
        return "  (no order book)"
    bids = book.bids[:3]
    asks = book.asks[:3]
    lines = []
    for level in asks[::-1]:
        lines.append(f"  ASK {level.price:f} x {level.quantity}")
    for level in bids:
        lines.append(f"  BID {level.price:f} x {level.quantity}")
    if not lines:
        return "  (empty book)"
    return "\n".join(lines)


def build_prompt(features: ProcessedFeatures) -> str:
    """`ProcessedFeatures` から決定論的にプロンプト文字列を組み立てる。

    同じ features は必ず同じ文字列を返す (テストで model_dump_json と一緒に
    snapshot 比較できるようにするため)。
    """
    book_block = _fmt_book(features.order_book)
    return (
        f"{_INSTRUCTION}\n\n"
        f"Symbol: {features.symbol}\n"
        f"Timestamp: {features.timestamp.isoformat()}\n"
        f"Current price: {_fmt(features.price)}\n"
        f"SMA short: {_fmt(features.sma_short)}\n"
        f"SMA long: {_fmt(features.sma_long)}\n"
        f"RSI: {_fmt(features.rsi)}\n"
        f"VWAP: {_fmt(features.vwap)}\n"
        f"Bollinger upper: {_fmt(features.bollinger_upper)}\n"
        f"Bollinger middle: {_fmt(features.bollinger_middle)}\n"
        f"Bollinger lower: {_fmt(features.bollinger_lower)}\n"
        f"Order book (top 3 each side):\n{book_block}\n"
    )
