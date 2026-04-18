from __future__ import annotations

import logging
from datetime import date
from typing import Any

import polars as pl

from ..calendar import business_days_back, is_tse_business_day, previous_business_day
from ..clients.jquants import JQuantsClient
from ..clients.supabase import SupabaseWriter

logger = logging.getLogger(__name__)


def daily_quotes_to_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    """J-Quants `/prices/daily_quotes` のレスポンスを DataFrame に正規化する。

    出力カラム: symbol, date, open, high, low, close, volume, turnover
    欠損 (null) は除外する。
    """
    schema: dict[str, pl.DataType] = {
        "symbol": pl.Utf8(),
        "date": pl.Date(),
        "open": pl.Float64(),
        "high": pl.Float64(),
        "low": pl.Float64(),
        "close": pl.Float64(),
        "volume": pl.Int64(),
        "turnover": pl.Float64(),
    }
    if not rows:
        return pl.DataFrame(schema=schema)

    records: list[dict[str, Any]] = []
    for r in rows:
        close = r.get("Close")
        if close is None:
            # 売買停止などで値が付かなかった日は Close が null。スキップ。
            continue
        records.append(
            {
                "symbol": str(r.get("Code", "")).strip(),
                "date": r.get("Date"),
                "open": r.get("Open"),
                "high": r.get("High"),
                "low": r.get("Low"),
                "close": close,
                "volume": r.get("Volume") or 0,
                "turnover": r.get("TurnoverValue") or 0.0,
            }
        )
    if not records:
        return pl.DataFrame(schema=schema)
    df = pl.DataFrame(records).with_columns(pl.col("date").str.to_date())
    return df.cast(schema)  # type: ignore[arg-type]


async def ingest_daily_ohlcv(
    jquants: JQuantsClient,
    supabase: SupabaseWriter,
    *,
    as_of: date,
    lookback_days: int,
) -> pl.DataFrame:
    """`as_of` までの直近 `lookback_days` 営業日分の日次 OHLCV を取得して Supabase に upsert する。

    - `as_of` が営業日でない場合は直前営業日にずらす
    - J-Quants `daily_quotes` は日付単位で全銘柄を返すので、期間分ループで取得する
    """
    end = as_of if is_tse_business_day(as_of) else previous_business_day(as_of)
    start = business_days_back(end, lookback_days)

    frames: list[pl.DataFrame] = []
    cursor = start
    while cursor <= end:
        if is_tse_business_day(cursor):
            rows = await jquants.daily_quotes(target_date=cursor)
            frame = daily_quotes_to_frame(rows)
            logger.info("daily_ohlcv: date=%s fetched=%d", cursor, frame.height)
            if frame.height > 0:
                frames.append(frame)
        cursor = _next_day(cursor)

    if not frames:
        return daily_quotes_to_frame([])

    df = pl.concat(frames, how="vertical")

    payload = [
        {
            "symbol": row["symbol"],
            "date": row["date"].isoformat(),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": int(row["volume"]),
            "turnover": row["turnover"],
        }
        for row in df.to_dicts()
    ]
    await supabase.upsert("daily_ohlcv", payload, on_conflict="symbol,date")
    return df


def _next_day(d: date) -> date:
    from datetime import timedelta

    return d + timedelta(days=1)
