"""Supabase (PostgREST) からの watchlist 読み込みクライアント。

Feeder が触る範囲は ``watchlist`` の R のみ。Universe Scanner が日次
8:00 JST に書き込んだ ``valid_date = 当日`` の行を取り、Feeder は
``KABU_DEFAULT_EXCHANGE`` を組み合わせて ``SymbolRegistration`` セットを
組む。

Realtime 購読ではなく **REST ポーリング** で実装する。理由:
- Universe Scanner は日次バッチで watchlist を書き換えるだけなので、
  数十秒〜数分のポーリング遅延で運用上問題ない
- Realtime (WebSocket) は依存が増え、テスト容易性も下がる
- 将来 Realtime に切り替えたい場合は ``WatchlistChangeFeed`` を別実装に
  差し替えれば session.py 側は無改修
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Self

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class SupabaseError(RuntimeError):
    """Supabase (PostgREST) 呼び出しで発生するエラー。"""


class WatchlistRow(BaseModel):
    """``watchlist`` テーブルの 1 行。Feeder が必要な最小フィールドのみ。"""

    symbol: str
    valid_date: date


@dataclass(slots=True)
class SupabaseWatchlistReader:
    """``watchlist`` テーブルからの読み取り専用クライアント。"""

    url: str
    secret_key: str
    timeout_seconds: float = 10.0
    transport: httpx.AsyncBaseTransport | None = None
    _client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(
            base_url=self.url.rstrip("/"),
            timeout=self.timeout_seconds,
            headers={
                "apikey": self.secret_key,
                "Authorization": f"Bearer {self.secret_key}",
            },
            transport=self.transport,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def fetch_watchlist(self, *, valid_date: date) -> list[WatchlistRow]:
        """``valid_date`` の watchlist を symbol 昇順で返す。

        該当行が無くても ``[]`` を返す (上流のエラーではないため例外にしない)。
        """
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/watchlist",
            params={
                "select": "symbol,valid_date",
                "valid_date": f"eq.{valid_date.isoformat()}",
                "order": "symbol.asc",
            },
        )
        if resp.status_code >= 500:
            raise SupabaseError(
                f"transient error: status={resp.status_code} body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise SupabaseError(
                f"watchlist read failed: status={resp.status_code} body={resp.text[:200]}"
            )
        rows: Any = resp.json()
        if not isinstance(rows, list):
            raise SupabaseError(f"unexpected watchlist payload: {type(rows).__name__}")
        try:
            return [WatchlistRow.model_validate(r) for r in rows]
        except ValidationError as exc:
            raise SupabaseError(f"invalid watchlist row: {exc}") from exc


@dataclass(slots=True)
class PollingWatchlistFeed:
    """``SupabaseWatchlistReader`` を一定間隔でポーリングする変更通知ストリーム。

    session.py は ``async for symbols in feed.subscribe():`` で受け取り、
    新しいスナップショットが届くたびに registry の差分計算 → register/unregister
    を呼ぶ。最初の 1 回は即時に流す。

    valid_date は呼び出しのたびに ``date_provider()`` で取得する。日跨ぎでも
    勝手に当日に切り替わる仕様。
    """

    reader: SupabaseWatchlistReader
    poll_interval_seconds: float = 60.0
    date_provider: Callable[[], date] = field(default_factory=lambda: date.today)
    sleep: Callable[[float], Any] = field(default=asyncio.sleep)

    async def subscribe(self) -> AsyncGenerator[list[WatchlistRow], None]:
        """watchlist スナップショットを poll し続ける async iterator。

        前回と同一の symbol セットでも ``yield`` する (registry 側で差分計算)。
        例外は呼び出し元に伝播させ、session.py の reconnect ループで吸収させる。
        """
        while True:
            target_date = self.date_provider()
            rows = await self.reader.fetch_watchlist(valid_date=target_date)
            logger.info("watchlist polled: valid_date=%s rows=%d", target_date, len(rows))
            yield rows
            await self.sleep(self.poll_interval_seconds)
