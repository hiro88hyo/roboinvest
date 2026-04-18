from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Self

import httpx
import polars as pl
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_PAGE_SIZE = 1000

_OHLCV_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.Utf8(),
    "date": pl.Date(),
    "open": pl.Float64(),
    "high": pl.Float64(),
    "low": pl.Float64(),
    "close": pl.Float64(),
    "volume": pl.Int64(),
    "turnover": pl.Float64(),
}


class SupabaseError(RuntimeError):
    """Supabase (PostgREST) とのやり取りで発生するエラー。"""


@dataclass(slots=True)
class SupabaseReader:
    """Supabase PostgREST 経由の読み取り専用クライアント。

    Backtest ランナーが `watchlist` / `daily_ohlcv` を参照する用途に限定する。
    大きな結果セットは Range ヘッダで分割取得する。
    """

    url: str
    secret_key: str
    timeout_seconds: float = 30.0
    transport: httpx.AsyncBaseTransport | None = None
    _client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(
            base_url=self.url.rstrip("/"),
            timeout=self.timeout_seconds,
            headers={
                "apikey": self.secret_key,
                "Authorization": f"Bearer {self.secret_key}",
                "Accept": "application/json",
            },
            transport=self.transport,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_watchlist(self, valid_date: date) -> list[str]:
        """`watchlist` の `valid_date` 分の symbol を返す。"""
        params = {
            "select": "symbol",
            "valid_date": f"eq.{valid_date.isoformat()}",
        }
        rows = await self._select_all("/rest/v1/watchlist", params=params)
        return [str(r["symbol"]) for r in rows]

    async def fetch_daily_ohlcv(
        self,
        symbols: list[str],
        *,
        start: date,
        end: date,
    ) -> pl.DataFrame:
        """`symbols` の `[start, end]` 区間の日次 OHLCV を DataFrame で返す。"""
        if not symbols:
            return pl.DataFrame(schema=_OHLCV_SCHEMA)

        params = {
            "select": "symbol,date,open,high,low,close,volume,turnover",
            "symbol": f"in.({','.join(symbols)})",
            "date": f"gte.{start.isoformat()}",
            "and": f"(date.lte.{end.isoformat()})",
            "order": "symbol.asc,date.asc",
        }
        rows = await self._select_all("/rest/v1/daily_ohlcv", params=params)
        if not rows:
            return pl.DataFrame(schema=_OHLCV_SCHEMA)
        df = pl.DataFrame(rows).with_columns(pl.col("date").str.to_date())
        return df.cast(_OHLCV_SCHEMA)  # type: ignore[arg-type]

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def _select_page(
        self,
        path: str,
        *,
        params: dict[str, str],
        offset: int,
    ) -> tuple[list[dict[str, Any]], int | None]:
        assert self._client is not None
        headers = {
            "Range-Unit": "items",
            "Range": f"{offset}-{offset + _PAGE_SIZE - 1}",
            "Prefer": "count=exact",
        }
        resp = await self._client.get(path, params=params, headers=headers)
        if resp.status_code >= 500:
            raise SupabaseError(
                f"transient error: path={path} status={resp.status_code} body={resp.text[:200]}"
            )
        if resp.status_code >= 300 and resp.status_code != 206:
            raise SupabaseError(
                f"select failed: path={path} status={resp.status_code} body={resp.text[:200]}"
            )
        data = resp.json()
        if not isinstance(data, list):
            raise SupabaseError(f"expected list response, got {type(data).__name__}")
        total = _parse_total(resp.headers.get("content-range"))
        return data, total

    async def _select_all(
        self,
        path: str,
        *,
        params: dict[str, str],
    ) -> list[dict[str, Any]]:
        offset = 0
        collected: list[dict[str, Any]] = []
        while True:
            page, total = await self._select_page(path, params=params, offset=offset)
            collected.extend(page)
            if not page:
                break
            if total is not None and offset + len(page) >= total:
                break
            if len(page) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        logger.info("supabase select: path=%s rows=%d", path, len(collected))
        return collected


def _parse_total(content_range: str | None) -> int | None:
    """`Content-Range: 0-999/12345` の末尾の総件数を返す。"""
    if not content_range or "/" not in content_range:
        return None
    tail = content_range.rsplit("/", 1)[1]
    if tail == "*":
        return None
    try:
        return int(tail)
    except ValueError:
        return None
