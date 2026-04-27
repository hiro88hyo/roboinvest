"""Unit-test fixtures.

`conftest.py` は他サービスとの mypy duplicate-module を避けるため置かない。
テスト側は `from strategy_ai._testing import ...` で読み込む。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from trade_contracts.features import ProcessedFeatures
from trade_contracts.market import OrderBookSnapshot, PriceLevel

from .llm.base import LLMClient, LLMError


def make_features(
    *,
    symbol: str = "7203",
    timestamp: datetime | None = None,
    price: Decimal = Decimal("1000"),
    sma_short: Decimal | None = None,
    sma_long: Decimal | None = None,
    rsi: Decimal | None = None,
    vwap: Decimal | None = None,
    bollinger_upper: Decimal | None = None,
    bollinger_middle: Decimal | None = None,
    bollinger_lower: Decimal | None = None,
    bids: Sequence[tuple[Decimal, int]] | None = None,
    asks: Sequence[tuple[Decimal, int]] | None = None,
    include_book: bool = True,
) -> ProcessedFeatures:
    ts = timestamp or datetime(2026, 4, 27, 9, 0, tzinfo=UTC)
    book: OrderBookSnapshot | None = None
    if include_book:
        bids_in = bids or [(Decimal("999"), 200), (Decimal("998"), 500)]
        asks_in = asks or [(Decimal("1001"), 200), (Decimal("1002"), 500)]
        book = OrderBookSnapshot(
            symbol=symbol,
            timestamp=ts,
            bids=[PriceLevel(price=p, quantity=q) for p, q in bids_in],
            asks=[PriceLevel(price=p, quantity=q) for p, q in asks_in],
        )
    return ProcessedFeatures(
        symbol=symbol,
        timestamp=ts,
        price=price,
        sma_short=sma_short,
        sma_long=sma_long,
        rsi=rsi,
        vwap=vwap,
        bollinger_upper=bollinger_upper,
        bollinger_middle=bollinger_middle,
        bollinger_lower=bollinger_lower,
        order_book=book,
    )


class FakeLLMClient(LLMClient):
    """テスト用 LLM。事前に渡したレスポンスを順番に返す。

    レスポンスが尽きたら `LLMError` を投げる。例外を流したい場合は
    `responses` に `LLMError` インスタンスを混ぜると `complete` で raise する。
    """

    def __init__(self, responses: Iterable[str | LLMError]) -> None:
        self._responses: list[str | LLMError] = list(responses)
        self.calls: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self._responses:
            raise LLMError("FakeLLMClient: no responses left")
        head = self._responses.pop(0)
        if isinstance(head, LLMError):
            raise head
        return head
