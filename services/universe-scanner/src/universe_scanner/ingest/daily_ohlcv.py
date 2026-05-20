from __future__ import annotations

import logging
from datetime import date
from typing import Any

import polars as pl

from ..calendar import business_days_back, is_tse_business_day, previous_business_day
from ..clients.jquants import JQuantsClient
from ..clients.supabase import SupabaseWriter
from ..symbols import collect_legacy_symbol_codes, normalize_symbol_code

logger = logging.getLogger(__name__)

_DELETE_CHUNK_SIZE = 200


def daily_quotes_to_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    """J-Quants daily quotes / equities bars daily のレスポンスを DataFrame に正規化する。

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
        close = r.get("Close") if "Close" in r else r.get("C")
        if close is None:
            # 売買停止などで値が付かなかった日は Close が null。スキップ。
            continue
        records.append(
            {
                "symbol": normalize_symbol_code(r.get("Code", "")),
                "date": r.get("Date"),
                "open": r.get("Open") if "Open" in r else r.get("O"),
                "high": r.get("High") if "High" in r else r.get("H"),
                "low": r.get("Low") if "Low" in r else r.get("L"),
                "close": close,
                "volume": (r.get("Volume") if "Volume" in r else r.get("Vo")) or 0,
                "turnover": (r.get("TurnoverValue") if "TurnoverValue" in r else r.get("Va"))
                or 0.0,
            }
        )
    if not records:
        return pl.DataFrame(schema=schema)
    df = pl.DataFrame(records).with_columns(pl.col("date").str.to_date()).cast(schema)  # type: ignore[arg-type]
    return df.unique(
        subset=["symbol", "date"],
        keep="first",
        maintain_order=True,
    )


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
    legacy_codes: set[str] = set()
    cursor = start
    while cursor <= end:
        if is_tse_business_day(cursor):
            rows = await jquants.daily_quotes(target_date=cursor)
            legacy_codes.update(collect_legacy_symbol_codes(r.get("Code", "") for r in rows))
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
    if legacy_codes:
        await _delete_legacy_symbols(supabase, "daily_ohlcv", sorted(legacy_codes))
    await supabase.upsert("daily_ohlcv", payload, on_conflict="symbol,date")
    return df


def _next_day(d: date) -> date:
    from datetime import timedelta

    return d + timedelta(days=1)


async def _delete_legacy_symbols(
    supabase: SupabaseWriter,
    table: str,
    legacy_codes: list[str],
) -> None:
    for start in range(0, len(legacy_codes), _DELETE_CHUNK_SIZE):
        chunk = legacy_codes[start : start + _DELETE_CHUNK_SIZE]
        await supabase.delete_where(
            table,
            filters={"symbol": f"in.({','.join(chunk)})"},
        )
