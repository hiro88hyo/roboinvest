from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl
from trade_contracts.features import ProcessedFeatures
from trade_contracts.market import OrderBookSnapshot, PriceLevel, TickData
from trade_contracts.tick_size import tse_tick_size

from feature_engine.config import FeatureEngineSettings
from feature_engine.indicators import bollinger, rsi, sma, volume_ratio, vwap

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
MARKET_OPEN = time(9, 0)
MORNING_CLOSE = time(11, 30)
AFTERNOON_OPEN = time(12, 30)
MARKET_CLOSE = time(15, 30)


def _default_buffer_size(settings: FeatureEngineSettings) -> int:
    """全指標のウォームアップをカバーする最小バッファサイズ。

    Wilder 型 RSI は EWM なのでウィンドウを超えても値が安定するまでさらにサンプルが要る。
    余裕を持たせて最大ウィンドウの 2 倍を下限とする。
    """
    max_window = max(
        settings.indicator_sma_long_window,
        settings.indicator_rsi_period,
        settings.indicator_vwap_window,
        settings.indicator_volume_ratio_window,
        settings.indicator_bollinger_period,
    )
    return max_window * 2


@dataclass(slots=True)
class StreamingFeatureState:
    """銘柄ごとに直近 tick のローリングバッファと最新板スナップショットを保持し、
    tick 受信のたびに `ProcessedFeatures` を組み立てるインメモリ状態。

    - tick の `price` は `close` 列にマップして既存の純関数指標をそのまま流用
    - ウォームアップ未達 (バッファが短い) 期間は指標値は `None` になる
    - 板情報は銘柄ごとに最新 1 件のみ保持し、次の tick に紐付けて出力する
    - プロセスを跨いだ永続化は持たない。RSI の EWM 状態はバッファから毎回再計算される
    """

    settings: FeatureEngineSettings
    buffer_size: int
    _ticks: dict[str, deque[dict[str, Any]]] = field(default_factory=dict)
    _books: dict[str, OrderBookSnapshot] = field(default_factory=dict)

    @classmethod
    def from_settings(
        cls,
        settings: FeatureEngineSettings,
        *,
        buffer_size: int | None = None,
    ) -> StreamingFeatureState:
        size = buffer_size if buffer_size is not None else _default_buffer_size(settings)
        if size <= 0:
            raise ValueError(f"buffer_size must be positive, got {size}")
        return cls(settings=settings, buffer_size=size)

    def record_order_book(self, book: OrderBookSnapshot) -> None:
        """銘柄ごとの最新板スナップショットを更新する。"""
        self._books[book.symbol] = book

    def record_tick(self, tick: TickData) -> ProcessedFeatures:
        """tick をバッファに追加し、最新の指標値で `ProcessedFeatures` を返す。"""
        buf = self._ticks.setdefault(tick.symbol, deque(maxlen=self.buffer_size))
        buf.append(
            {
                "symbol": tick.symbol,
                "close": float(tick.price),
                "volume": int(tick.volume),
            }
        )
        df = pl.DataFrame(list(buf))
        df = sma(df, self.settings.indicator_sma_short_window, output_col="sma_short")
        df = sma(df, self.settings.indicator_sma_long_window, output_col="sma_long")
        df = rsi(df, self.settings.indicator_rsi_period, output_col="rsi")
        df = vwap(
            df,
            self.settings.indicator_vwap_window,
            price_col="close",
            output_col="vwap",
        )
        df = volume_ratio(
            df,
            self.settings.indicator_volume_ratio_window,
            output_col="volume_ratio",
        )
        df = bollinger(
            df,
            self.settings.indicator_bollinger_period,
            self.settings.indicator_bollinger_stddev,
            prefix="bollinger",
        )
        last = df.tail(1).to_dicts()[0]
        book = self._books.get(tick.symbol)
        book_metrics = _book_metrics(book)
        session_metrics = _session_metrics(tick.timestamp)
        return ProcessedFeatures(
            symbol=tick.symbol,
            timestamp=tick.timestamp,
            price=tick.price,
            sma_short=_to_decimal(last.get("sma_short")),
            sma_long=_to_decimal(last.get("sma_long")),
            rsi=_to_decimal(last.get("rsi")),
            vwap=_to_decimal(last.get("vwap")),
            volume_ratio=_to_decimal(last.get("volume_ratio")),
            bollinger_upper=_to_decimal(last.get("bollinger_upper")),
            bollinger_middle=_to_decimal(last.get("bollinger_middle")),
            bollinger_lower=_to_decimal(last.get("bollinger_lower")),
            order_book=book,
            best_bid=book_metrics.best_bid,
            best_ask=book_metrics.best_ask,
            spread_bps=book_metrics.spread_bps,
            tick_size=book_metrics.tick_size,
            spread_ticks=book_metrics.spread_ticks,
            bid_depth_1=book_metrics.bid_depth_1,
            ask_depth_1=book_metrics.ask_depth_1,
            bid_depth_5=book_metrics.bid_depth_5,
            ask_depth_5=book_metrics.ask_depth_5,
            book_imbalance_5=book_metrics.book_imbalance_5,
            minutes_from_open=session_metrics.minutes_from_open,
            minutes_to_close=session_metrics.minutes_to_close,
            session_phase=session_metrics.session_phase,
        )

    def buffer_length(self, symbol: str) -> int:
        """テスト・デバッグ用: 銘柄ごとの現在のバッファ長。"""
        buf = self._ticks.get(symbol)
        return len(buf) if buf is not None else 0

    def reset(self, symbol: str | None = None) -> None:
        """バッファと板スナップショットをクリアする。"""
        if symbol is None:
            self._ticks.clear()
            self._books.clear()
        else:
            self._ticks.pop(symbol, None)
            self._books.pop(symbol, None)


def _to_decimal(value: float | int | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


@dataclass(frozen=True, slots=True)
class _BookMetrics:
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


@dataclass(frozen=True, slots=True)
class _SessionMetrics:
    minutes_from_open: int | None
    minutes_to_close: int | None
    session_phase: str


def _book_metrics(book: OrderBookSnapshot | None) -> _BookMetrics:
    if book is None:
        return _BookMetrics()

    best_bid = book.bids[0].price if book.bids else None
    best_ask = book.asks[0].price if book.asks else None
    bid_depth_1 = _depth(book.bids, 1) if book.bids else None
    ask_depth_1 = _depth(book.asks, 1) if book.asks else None
    bid_depth_5 = _depth(book.bids, 5) if book.bids else None
    ask_depth_5 = _depth(book.asks, 5) if book.asks else None
    return _BookMetrics(
        best_bid=best_bid,
        best_ask=best_ask,
        spread_bps=_spread_bps(best_bid=best_bid, best_ask=best_ask),
        tick_size=_tick_size(best_bid=best_bid, best_ask=best_ask),
        spread_ticks=_spread_ticks(best_bid=best_bid, best_ask=best_ask),
        bid_depth_1=bid_depth_1,
        ask_depth_1=ask_depth_1,
        bid_depth_5=bid_depth_5,
        ask_depth_5=ask_depth_5,
        book_imbalance_5=_book_imbalance(bid_depth=bid_depth_5, ask_depth=ask_depth_5),
    )


def _depth(levels: list[PriceLevel], n: int) -> int:
    return sum(level.quantity for level in levels[:n])


def _spread_bps(*, best_bid: Decimal | None, best_ask: Decimal | None) -> Decimal | None:
    if best_bid is None or best_ask is None:
        return None
    mid = (best_bid + best_ask) / Decimal("2")
    if mid <= 0:
        return None
    return ((best_ask - best_bid) / mid) * Decimal("10000")


def _tick_size(*, best_bid: Decimal | None, best_ask: Decimal | None) -> Decimal | None:
    if best_bid is None or best_ask is None:
        return None
    mid = (best_bid + best_ask) / Decimal("2")
    if mid <= 0:
        return None
    return tse_tick_size(mid)


def _spread_ticks(*, best_bid: Decimal | None, best_ask: Decimal | None) -> Decimal | None:
    tick_size = _tick_size(best_bid=best_bid, best_ask=best_ask)
    if best_bid is None or best_ask is None or tick_size is None:
        return None
    spread = best_ask - best_bid
    if spread < 0:
        return None
    return spread / tick_size


def _book_imbalance(*, bid_depth: int | None, ask_depth: int | None) -> Decimal | None:
    if bid_depth is None or ask_depth is None:
        return None
    total = bid_depth + ask_depth
    if total <= 0:
        return None
    return Decimal(bid_depth - ask_depth) / Decimal(total)


def _session_metrics(timestamp: datetime) -> _SessionMetrics:
    local = timestamp.astimezone(JST)
    local_time = local.time()
    market_open = local.replace(hour=9, minute=0, second=0, microsecond=0)
    market_close = local.replace(hour=15, minute=30, second=0, microsecond=0)
    minutes_from_open = int((local - market_open).total_seconds() // 60)
    minutes_to_close = int((market_close - local).total_seconds() // 60)

    if local_time < MARKET_OPEN:
        phase = "pre_open"
    elif local_time < MORNING_CLOSE:
        phase = "morning"
    elif local_time < AFTERNOON_OPEN:
        phase = "lunch"
    elif local_time < MARKET_CLOSE:
        phase = "afternoon"
    else:
        phase = "after_close"
    return _SessionMetrics(
        minutes_from_open=minutes_from_open,
        minutes_to_close=minutes_to_close,
        session_phase=phase,
    )
