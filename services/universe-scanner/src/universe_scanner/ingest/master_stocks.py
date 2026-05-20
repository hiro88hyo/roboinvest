from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

import polars as pl

from ..clients.jquants import JQuantsClient
from ..clients.supabase import SupabaseWriter
from ..symbols import collect_legacy_symbol_codes, normalize_symbol_code

logger = logging.getLogger(__name__)

_DELETE_CHUNK_SIZE = 200


# J-Quants `MarketCodeName` -> `master_stocks.market_segment` の正規化。
# 一次情報の表記ゆれに備えて予めマップする。未知値はそのまま保存。
# キーは J-Quants が返す全角カッコを含む文字列リテラルのため、RUF001 を抑止。
_MARKET_SEGMENT_ALIASES = {
    "プライム（内国株式）": "プライム",  # noqa: RUF001
    "スタンダード（内国株式）": "スタンダード",  # noqa: RUF001
    "グロース（内国株式）": "グロース",  # noqa: RUF001
}


def _normalize_segment(raw: str | None) -> str:
    if raw is None:
        return ""
    return _MARKET_SEGMENT_ALIASES.get(raw, raw)


def listed_info_to_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    """J-Quants listed info/master のレスポンスを DataFrame に正規化する。

    出力カラム: symbol, symbol_name, market_segment, sector, is_active
    """
    if not rows:
        return pl.DataFrame(
            schema={
                "symbol": pl.Utf8,
                "symbol_name": pl.Utf8,
                "market_segment": pl.Utf8,
                "sector": pl.Utf8,
                "is_active": pl.Boolean,
            }
        )
    records = [
        {
            "symbol": normalize_symbol_code(r.get("Code", "")),
            "symbol_name": str(r.get("CompanyName") or r.get("CoName") or "").strip(),
            "market_segment": _normalize_segment(r.get("MarketCodeName") or r.get("MktNm")),
            "sector": (
                r.get("Sector17CodeName")
                or r.get("S17Nm")
                or r.get("Sector33CodeName")
                or r.get("S33Nm")
                or None
            ),
            # J-Quants listed/info は上場中銘柄のみ返す想定
            "is_active": True,
        }
        for r in rows
    ]
    return pl.DataFrame(records).unique(
        subset=["symbol"],
        keep="first",
        maintain_order=True,
    )


async def ingest_master_stocks(
    jquants: JQuantsClient,
    supabase: SupabaseWriter,
    *,
    as_of: date,
) -> pl.DataFrame:
    """銘柄マスタを取得して Supabase `master_stocks` に upsert し、DataFrame を返す。"""
    raw = await jquants.listed_info(as_of=as_of)
    df = listed_info_to_frame(raw)
    logger.info("master_stocks: fetched=%d", df.height)

    legacy_codes = collect_legacy_symbol_codes(r.get("Code", "") for r in raw)
    if legacy_codes:
        await _delete_legacy_symbols(supabase, "master_stocks", legacy_codes)

    now = datetime.now(UTC).isoformat()
    payload = [{**row, "updated_at": now} for row in df.to_dicts()]
    await supabase.upsert("master_stocks", payload, on_conflict="symbol")
    return df


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
