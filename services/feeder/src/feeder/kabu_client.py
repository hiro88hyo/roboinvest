"""kabuステーション API への薄いクライアント。

REST は ``KabuClient`` 経由で叩き、WebSocket は ``connect_websocket`` の
async context manager 経由で取得する。Phase 1 ではメッセージのパースまでは
踏み込まず、JSON dict のまま返す。

kabu の 4xx は HTML ではなく JSON で返ってくるが、``raise_for_status`` で握り
つぶすとデバッグ困難になるため ``KabuApiError`` で本文を露出させる。
``scripts/probe-kabu.py`` の方針に揃えてある。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import websockets
from trade_contracts.kabu_token import KabuTokenCache

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection


class KabuApiError(RuntimeError):
    """kabu API が 4xx/5xx を返したときに本文を露出させて投げる例外。"""

    def __init__(self, status_code: int, body: dict[str, Any] | str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"kabu API returned HTTP {status_code}: {body!r}")


@dataclass(frozen=True)
class SymbolRegistration:
    """``/register`` / ``/unregister`` に渡す銘柄指定。"""

    symbol: str
    exchange: int

    def to_payload(self) -> dict[str, Any]:
        return {"Symbol": self.symbol, "Exchange": self.exchange}


class KabuClient:
    """kabuステーション API への薄いラッパー。

    1 インスタンスで 1 トークンを保持する。``fetch_token`` を明示的に呼ぶか、
    ``ensure_token`` で必要時に取得する運用。同じ API パスワードで複数インス
    タンスが ``fetch_token`` を叩くと先発トークンが無効化されるので、Feeder
    プロセス全体で 1 インスタンスを共有する前提。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_password: str,
        ws_url: str,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
        ws_ping_interval: float | None = None,
        ws_ping_timeout: float | None = None,
        ws_max_queue: int | None = 2048,
        token_cache: KabuTokenCache | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._ws_url = ws_url
        self._api_password = api_password
        self._timeout_seconds = timeout_seconds
        self._ws_ping_interval = ws_ping_interval
        self._ws_ping_timeout = ws_ping_timeout
        self._ws_max_queue = ws_max_queue
        self._owns_client = http_client is None
        self._client: httpx.AsyncClient = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._token: str | None = None
        self._token_cache = token_cache

    @property
    def token(self) -> str | None:
        return self._token

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> KabuClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def fetch_token(self) -> str:
        """``POST /token`` でトークンを発行・保持する。"""
        r = await self._client.post(
            f"{self._base_url}/token",
            json={"APIPassword": self._api_password},
            headers={"Content-Type": "application/json"},
        )
        body = _check(r)
        token = body.get("Token")
        if not isinstance(token, str) or not token:
            raise KabuApiError(r.status_code, body)
        self._token = token
        if self._token_cache is not None:
            self._token_cache.save(token)
        return token

    async def ensure_token(self) -> str:
        if self._token is not None:
            return self._token
        if self._token_cache is not None:
            cached = self._token_cache.load()
            if cached is not None:
                self._token = cached
                return cached
        return await self.fetch_token()

    def invalidate_token(self) -> None:
        """401/403 を踏んだ場合に外部から呼んでクリアする用。"""
        self._token = None
        if self._token_cache is not None:
            self._token_cache.invalidate()

    async def register(self, symbols: Sequence[SymbolRegistration]) -> dict[str, Any]:
        """``PUT /register`` で銘柄を購読対象に追加する。"""
        return await self._put(
            "/register",
            {"Symbols": [s.to_payload() for s in symbols]},
        )

    async def unregister(self, symbols: Sequence[SymbolRegistration]) -> dict[str, Any]:
        """``PUT /unregister`` で銘柄を購読対象から外す。"""
        return await self._put(
            "/unregister",
            {"Symbols": [s.to_payload() for s in symbols]},
        )

    async def unregister_all(self) -> dict[str, Any]:
        """``PUT /unregister/all`` で全銘柄の購読を解除する。"""
        return await self._put("/unregister/all", body=None)

    async def get_symbol(self, symbol: str, exchange: int) -> dict[str, Any]:
        """``GET /symbol/{code}@{exchange}`` で銘柄情報を取得する。"""
        token = await self.ensure_token()
        r = await self._client.get(
            f"{self._base_url}/symbol/{symbol}@{exchange}",
            headers={"X-API-KEY": token},
        )
        return _check(r)

    @asynccontextmanager
    async def connect_websocket(self) -> AsyncIterator[ClientConnection]:
        """WS 接続を開く。``X-API-KEY`` ヘッダ必須。

        ``ws_ping_interval`` / ``ws_ping_timeout`` は ``websockets.connect`` の
        keepalive 設定。``None`` を指定すると client 側の自動 ping を無効化する。
        ``ws_max_queue`` は受信 burst を吸収するアプリ側キュー長。
        kabu/Caddy が client ping に pong を返さない場合のデフォルト挙動。
        """
        token = await self.ensure_token()
        async with websockets.connect(
            self._ws_url,
            additional_headers=[("X-API-KEY", token)],
            open_timeout=10.0,
            ping_interval=self._ws_ping_interval,
            ping_timeout=self._ws_ping_timeout,
            max_queue=self._ws_max_queue,
        ) as ws:
            yield ws

    async def _put(self, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        token = await self.ensure_token()
        kwargs: dict[str, Any] = {
            "headers": {
                "X-API-KEY": token,
                "Content-Type": "application/json",
            }
        }
        if body is not None:
            kwargs["json"] = body
        r = await self._client.put(f"{self._base_url}{path}", **kwargs)
        return _check(r)


def _check(r: httpx.Response) -> dict[str, Any]:
    """kabu の 4xx は JSON ボディに ``Code``/``Message`` が入る。本文を露出させる。"""
    try:
        body = r.json()
    except (json.JSONDecodeError, ValueError):
        body = {"_raw": r.text}
    if r.is_success:
        if not isinstance(body, dict):
            return {"_raw": body}
        return body
    raise KabuApiError(r.status_code, body)
