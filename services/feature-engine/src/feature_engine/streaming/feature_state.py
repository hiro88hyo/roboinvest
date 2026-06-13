from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import polars as pl
from trade_contracts.features import ProcessedFeatures
from trade_contracts.market import OrderBookSnapshot, TickData

from feature_engine.config import FeatureEngineSettings
from feature_engine.indicators import bollinger, rsi, sma, volume_ratio, vwap

logger = logging.getLogger(__name__)


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
            order_book=self._books.get(tick.symbol),
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
