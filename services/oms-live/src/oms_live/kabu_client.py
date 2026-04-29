"""kabuステーション API への薄いクライアント (OMS Live 用)。

Feeder の ``KabuClient`` は ``/register`` / ``/unregister`` / WS 接続が責務だが、
こちらは ``/sendorder`` / ``/cancelorder`` / ``/orders`` / ``/positions`` /
``/symbol`` を扱う。重複コードに見えるが「責務ごとにトークンを分ける」ため
あえて別クラスとして分離する (Feeder と OMS Live は **別 API パスワード推奨**、
詳細は CLAUDE.md)。

4xx/5xx は ``KabuApiError`` で本文を露出させる方針は Feeder と同じ。
"""

from __future__ import annotations

import json
from typing import Any

import httpx


class KabuApiError(RuntimeError):
    """kabu API が 4xx/5xx を返したときに本文を露出させて投げる例外。"""

    def __init__(self, status_code: int, body: dict[str, Any] | str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"kabu API returned HTTP {status_code}: {body!r}")


class KabuLiveClient:
    """kabuステーション API への発注系ラッパー。

    1 インスタンスで 1 トークンを保持する。同じ API パスワードで複数インス
    タンスが ``fetch_token`` を叩くと先発トークンが無効化されるので、OMS Live
    プロセス全体で 1 インスタンスを共有する前提。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_password: str,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_password = api_password
        self._timeout_seconds = timeout_seconds
        self._owns_client = http_client is None
        self._client: httpx.AsyncClient = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._token: str | None = None

    @property
    def token(self) -> str | None:
        return self._token

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> KabuLiveClient:
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
        return token

    async def ensure_token(self) -> str:
        if self._token is None:
            return await self.fetch_token()
        return self._token

    def invalidate_token(self) -> None:
        """401/403 を踏んだ場合に外部から呼んでクリアする用。"""
        self._token = None

    async def send_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """``POST /sendorder`` で発注する。

        payload は ``order_builder.build_sendorder_payload`` で組み立てたもの。
        kabu のレスポンスは ``{"Result": 0, "OrderId": "..."}``。``Result != 0``
        を例外化するかは呼び出し側の判断 (Phase 2 Runner で扱う)。
        """
        return await self._post("/sendorder", payload)

    async def cancel_order(self, *, order_id: str, password: str) -> dict[str, Any]:
        """``PUT /cancelorder`` で発注済み注文を取消する。"""
        return await self._put(
            "/cancelorder",
            {"OrderId": order_id, "Password": password},
        )

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """``GET /orders?id=<id>`` で注文 1 件の状態を取得する。

        kabu の ``/orders`` はフィルタ条件付き一覧を返す。``id`` フィルタで
        最大 1 件になるはずだが、空配列が返ったら ``KabuApiError(404, ...)``
        相当として扱いやすいよう本ラッパーで解釈する。
        """
        token = await self.ensure_token()
        r = await self._client.get(
            f"{self._base_url}/orders",
            params={"id": order_id},
            headers={"X-API-KEY": token},
        )
        body = _check_list(r)
        if not body:
            raise KabuApiError(404, {"_raw": f"order_id {order_id!r} not found"})
        first = body[0]
        if not isinstance(first, dict):
            raise KabuApiError(r.status_code, {"_raw": body})
        return first

    async def list_positions(
        self,
        *,
        product: int | None = None,
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        """``GET /positions`` で残高一覧を取得する。

        ``product`` は kabu API の ``product`` パラメータ (0=すべて, 1=現物 等)。
        Phase 2 で起動時の Supabase 整合性チェックに使う。
        """
        token = await self.ensure_token()
        params: dict[str, Any] = {}
        if product is not None:
            params["product"] = product
        if symbol is not None:
            params["symbol"] = symbol
        r = await self._client.get(
            f"{self._base_url}/positions",
            params=params or None,
            headers={"X-API-KEY": token},
        )
        return _check_list(r)

    async def get_symbol(self, symbol: str, exchange: int) -> dict[str, Any]:
        """``GET /symbol/{code}@{exchange}`` で銘柄情報を取得する。"""
        token = await self.ensure_token()
        r = await self._client.get(
            f"{self._base_url}/symbol/{symbol}@{exchange}",
            headers={"X-API-KEY": token},
        )
        return _check(r)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        token = await self.ensure_token()
        r = await self._client.post(
            f"{self._base_url}{path}",
            json=body,
            headers={
                "X-API-KEY": token,
                "Content-Type": "application/json",
            },
        )
        return _check(r)

    async def _put(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        token = await self.ensure_token()
        r = await self._client.put(
            f"{self._base_url}{path}",
            json=body,
            headers={
                "X-API-KEY": token,
                "Content-Type": "application/json",
            },
        )
        return _check(r)


def _decode(r: httpx.Response) -> Any:
    try:
        return r.json()
    except (json.JSONDecodeError, ValueError):
        return {"_raw": r.text}


def _check(r: httpx.Response) -> dict[str, Any]:
    """成功時は dict を返す。4xx/5xx は ``KabuApiError`` で本文を露出させる。"""
    body = _decode(r)
    if r.is_success:
        if not isinstance(body, dict):
            return {"_raw": body}
        return body
    if isinstance(body, dict | str):
        raise KabuApiError(r.status_code, body)
    raise KabuApiError(r.status_code, {"_raw": body})


def _check_list(r: httpx.Response) -> list[dict[str, Any]]:
    """list を返すエンドポイント (``/orders``, ``/positions``) 用。"""
    body = _decode(r)
    if not r.is_success:
        if isinstance(body, dict | str):
            raise KabuApiError(r.status_code, body)
        raise KabuApiError(r.status_code, {"_raw": body})
    if not isinstance(body, list):
        raise KabuApiError(r.status_code, {"_raw": body})
    out: list[dict[str, Any]] = []
    for item in body:
        if not isinstance(item, dict):
            raise KabuApiError(r.status_code, {"_raw": body})
        out.append(item)
    return out
