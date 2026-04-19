from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

import polars as pl
from trade_contracts.features import ProcessedFeatures

from ..config import FeatureEngineSettings
from ..indicators import bollinger, rsi, sma, vwap

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
MARKET_CLOSE = time(15, 0, 0)


class OHLCVSource(Protocol):
    """Backtest ランナーが依存するデータソースの契約。"""

    async def fetch_watchlist(self, valid_date: date) -> list[str]: ...

    async def fetch_daily_ohlcv(
        self,
        symbols: list[str],
        *,
        start: date,
        end: date,
    ) -> pl.DataFrame: ...


@dataclass(frozen=True, slots=True)
class BacktestResult:
    end_date: date
    symbols: list[str]
    features: list[ProcessedFeatures]
    dataframe: pl.DataFrame


def compute_features(
    ohlcv: pl.DataFrame,
    settings: FeatureEngineSettings,
) -> pl.DataFrame:
    """`daily_ohlcv` に指標列を付与した DataFrame を返す。

    入力カラム: symbol, date, open, high, low, close, volume, turnover
    出力は入力 + sma_short / sma_long / rsi / vwap / bollinger_upper/middle/lower。
    """
    if ohlcv.is_empty():
        return ohlcv

    df = ohlcv.sort(["symbol", "date"])
    df = sma(df, settings.indicator_sma_short_window, output_col="sma_short")
    df = sma(df, settings.indicator_sma_long_window, output_col="sma_long")
    df = rsi(df, settings.indicator_rsi_period, output_col="rsi")
    df = vwap(df, settings.indicator_vwap_window, output_col="vwap")
    df = bollinger(
        df,
        settings.indicator_bollinger_period,
        settings.indicator_bollinger_stddev,
        prefix="bollinger",
    )
    return df


def to_processed_features(df: pl.DataFrame) -> list[ProcessedFeatures]:
    """指標付き DataFrame を `ProcessedFeatures` のリストへ変換する。

    - timestamp は当該営業日の 15:00 JST (大引け) として生成
    - Float64 の数値は `Decimal(str(v))` で Decimal に昇格。null は `None`
    - `close` が null の行はスキップ
    """
    if df.is_empty():
        return []

    required = {
        "symbol",
        "date",
        "close",
        "sma_short",
        "sma_long",
        "rsi",
        "vwap",
        "bollinger_upper",
        "bollinger_middle",
        "bollinger_lower",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"missing indicator columns: {sorted(missing)}")

    out: list[ProcessedFeatures] = []
    for row in df.iter_rows(named=True):
        close = row["close"]
        if close is None:
            continue
        out.append(
            ProcessedFeatures(
                symbol=row["symbol"],
                timestamp=datetime.combine(row["date"], MARKET_CLOSE, tzinfo=JST),
                price=_to_decimal(close),
                sma_short=_to_decimal(row["sma_short"]),
                sma_long=_to_decimal(row["sma_long"]),
                rsi=_to_decimal(row["rsi"]),
                vwap=_to_decimal(row["vwap"]),
                bollinger_upper=_to_decimal(row["bollinger_upper"]),
                bollinger_middle=_to_decimal(row["bollinger_middle"]),
                bollinger_lower=_to_decimal(row["bollinger_lower"]),
                order_book=None,
            )
        )
    return out


async def run_backtest(
    source: OHLCVSource,
    settings: FeatureEngineSettings,
    *,
    end_date: date,
    symbols: list[str] | None = None,
) -> BacktestResult:
    """Backtest パイプラインの本体。

    1. symbols が未指定なら `watchlist(valid_date=end_date)` から解決
    2. `[end_date - lookback_days, end_date]` の `daily_ohlcv` を取得
    3. 指標を計算し `ProcessedFeatures` へ変換
    """
    resolved = symbols if symbols else await source.fetch_watchlist(end_date)
    if not resolved:
        logger.warning("empty watchlist for end_date=%s", end_date)
        return BacktestResult(end_date=end_date, symbols=[], features=[], dataframe=pl.DataFrame())

    start_date = end_date - timedelta(days=settings.backtest_lookback_days)
    ohlcv = await source.fetch_daily_ohlcv(resolved, start=start_date, end=end_date)
    logger.info(
        "backtest fetched: symbols=%d rows=%d range=%s..%s",
        len(resolved),
        ohlcv.height,
        start_date,
        end_date,
    )
    features_df = compute_features(ohlcv, settings)
    features = to_processed_features(features_df)
    return BacktestResult(
        end_date=end_date,
        symbols=resolved,
        features=features,
        dataframe=features_df,
    )


def _to_decimal(value: float | int | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
