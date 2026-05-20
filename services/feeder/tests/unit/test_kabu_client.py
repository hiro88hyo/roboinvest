"""KabuClient の単体テスト。httpx の MockTransport で REST 系を検証する。"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import websockets
from feeder.kabu_client import KabuApiError, KabuClient, SymbolRegistration
from trade_contracts.kabu_token import KabuTokenCache


def _make_client(
    handler: httpx.MockTransport,
    token_cache: KabuTokenCache | None = None,
) -> KabuClient:
    return KabuClient(
        base_url="http://localhost:18081/kabusapi",
        api_password="dummy-pw",
        ws_url="ws://localhost:18081/kabusapi/websocket",
        http_client=httpx.AsyncClient(transport=handler),
        token_cache=token_cache,
    )


async def test_fetch_token_returns_token_and_caches() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        assert body == {"APIPassword": "dummy-pw"}
        return httpx.Response(200, json={"Token": "abcdef" * 5})

    client = _make_client(httpx.MockTransport(handler))
    try:
        token = await client.fetch_token()
        assert token == "abcdef" * 5
        assert client.token == token
        # ensure_token は再発行を起こさない
        again = await client.ensure_token()
        assert again == token
        assert len(seen) == 1
    finally:
        await client.aclose()


async def test_fetch_token_raises_on_4xx_with_body() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"Code": 4001001, "Message": "API パスワード不一致"})

    client = _make_client(httpx.MockTransport(handler))
    try:
        with pytest.raises(KabuApiError) as exc:
            await client.fetch_token()
        assert exc.value.status_code == 401
        assert exc.value.body == {"Code": 4001001, "Message": "API パスワード不一致"}
    finally:
        await client.aclose()


async def test_register_sends_symbols_with_api_key() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"Token": "tok-123"})
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["api_key"] = request.headers.get("X-API-KEY")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"RegistList": [{"Symbol": "7203", "Exchange": 1}]})

    client = _make_client(httpx.MockTransport(handler))
    try:
        result = await client.register(
            [
                SymbolRegistration(symbol="7203", exchange=1),
                SymbolRegistration(symbol="9984", exchange=1),
            ]
        )
        assert captured["method"] == "PUT"
        assert captured["api_key"] == "tok-123"
        assert captured["body"] == {
            "Symbols": [
                {"Symbol": "7203", "Exchange": 1},
                {"Symbol": "9984", "Exchange": 1},
            ]
        }
        assert "RegistList" in result
    finally:
        await client.aclose()


async def test_unregister_all_uses_put_with_no_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"Token": "tok-abc"})
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["content_length"] = len(request.content)
        return httpx.Response(200, json={"RegistList": []})

    client = _make_client(httpx.MockTransport(handler))
    try:
        await client.unregister_all()
        assert captured["method"] == "PUT"
        assert captured["url"] == "http://localhost:18081/kabusapi/unregister/all"
        assert captured["content_length"] == 0
    finally:
        await client.aclose()


async def test_get_symbol_propagates_kabu_error_body_for_4xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"Token": "tok-xyz"})
        return httpx.Response(
            400,
            json={"Code": 4001003, "Message": "銘柄コードが不正です"},
        )

    client = _make_client(httpx.MockTransport(handler))
    try:
        with pytest.raises(KabuApiError) as exc:
            await client.get_symbol("XXXX", 1)
        assert exc.value.status_code == 400
        assert exc.value.body == {"Code": 4001003, "Message": "銘柄コードが不正です"}
    finally:
        await client.aclose()


async def test_get_symbol_returns_dict_for_2xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"Token": "tok-1"})
        assert request.url.path.endswith("/symbol/7203@1")
        return httpx.Response(
            200,
            json={"Symbol": "7203", "SymbolName": "トヨタ", "PriceRangeGroup": "10000"},
        )

    client = _make_client(httpx.MockTransport(handler))
    try:
        info = await client.get_symbol("7203", 1)
        assert info["Symbol"] == "7203"
        assert info["SymbolName"] == "トヨタ"
    finally:
        await client.aclose()


async def test_invalidate_token_forces_refetch() -> None:
    counter = {"token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            counter["token"] += 1
            return httpx.Response(200, json={"Token": f"tok-{counter['token']}"})
        return httpx.Response(200, json={})

    client = _make_client(httpx.MockTransport(handler))
    try:
        first = await client.ensure_token()
        client.invalidate_token()
        second = await client.ensure_token()
        assert first != second
        assert counter["token"] == 2
    finally:
        await client.aclose()


async def test_connect_websocket_passes_ping_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ws_ping_interval`` / ``ws_ping_timeout`` / ``ws_max_queue`` がそのまま
    ``websockets.connect`` に渡ること。kabu/Caddy が pong を返さない疑いが
    あるため、デフォルト (``None`` = client-side ping 無効) も含めて検証する。"""
    captured: dict[str, Any] = {}

    class _DummyConn:
        async def __aenter__(self) -> _DummyConn:
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

    @asynccontextmanager
    async def fake_connect(url: str, **kwargs: Any) -> Any:
        captured["url"] = url
        captured["kwargs"] = kwargs
        yield _DummyConn()

    # websockets.connect そのものは async context manager を返す関数。
    # kabu_client.py は `import websockets` 経由で参照しているので、
    # トップレベルモジュール側を差し替えれば feeder からも fake が見える
    monkeypatch.setattr(websockets, "connect", fake_connect)

    def token_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Token": "tok-ws"})

    # ケース1: デフォルト (両方 None) で client-side ping 無効
    client = KabuClient(
        base_url="http://localhost:18081/kabusapi",
        api_password="pw",
        ws_url="ws://localhost:18081/kabusapi/websocket",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(token_handler)),
    )
    try:
        async with client.connect_websocket():
            pass
        assert captured["url"] == "ws://localhost:18081/kabusapi/websocket"
        assert captured["kwargs"]["ping_interval"] is None
        assert captured["kwargs"]["ping_timeout"] is None
        assert captured["kwargs"]["max_queue"] == 2048
        # X-API-KEY ヘッダも引き続き付与されている
        headers = dict(captured["kwargs"]["additional_headers"])
        assert headers["X-API-KEY"] == "tok-ws"
    finally:
        await client.aclose()

    # ケース2: 明示値が渡されればそのまま websockets.connect に届く
    client2 = KabuClient(
        base_url="http://localhost:18081/kabusapi",
        api_password="pw",
        ws_url="ws://localhost:18081/kabusapi/websocket",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(token_handler)),
        ws_ping_interval=30.0,
        ws_ping_timeout=15.0,
        ws_max_queue=512,
    )
    try:
        async with client2.connect_websocket():
            pass
        assert captured["kwargs"]["ping_interval"] == 30.0
        assert captured["kwargs"]["ping_timeout"] == 15.0
        assert captured["kwargs"]["max_queue"] == 512
    finally:
        await client2.aclose()


async def test_check_handles_non_json_body() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"<html>internal error</html>")

    client = _make_client(httpx.MockTransport(handler))
    try:
        with pytest.raises(KabuApiError) as exc:
            await client.fetch_token()
        assert exc.value.status_code == 500
        assert isinstance(exc.value.body, dict)
        assert exc.value.body.get("_raw", "").startswith("<html>")
    finally:
        await client.aclose()


# --- token cache 統合テスト ---


async def test_ensure_token_reads_from_cache_without_fetch(tmp_path: Path) -> None:
    cache = KabuTokenCache(tmp_path / "tok.json")
    cache.save("cached-token")
    fetch_called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            fetch_called["n"] += 1
        return httpx.Response(200, json={"Token": "new-token"})

    client = _make_client(httpx.MockTransport(handler), token_cache=cache)
    try:
        token = await client.ensure_token()
        assert token == "cached-token"
        assert fetch_called["n"] == 0
    finally:
        await client.aclose()


async def test_fetch_token_writes_to_cache(tmp_path: Path) -> None:
    cache = KabuTokenCache(tmp_path / "tok.json")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Token": "fresh-token"})

    client = _make_client(httpx.MockTransport(handler), token_cache=cache)
    try:
        await client.fetch_token()
        assert cache.load() == "fresh-token"
    finally:
        await client.aclose()


async def test_invalidate_token_clears_cache(tmp_path: Path) -> None:
    cache = KabuTokenCache(tmp_path / "tok.json")
    cache.save("some-token")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Token": "tok"})

    client = _make_client(httpx.MockTransport(handler), token_cache=cache)
    try:
        await client.ensure_token()
        client.invalidate_token()
        assert cache.load() is None
    finally:
        await client.aclose()


async def test_ensure_token_fetches_when_cache_is_empty(tmp_path: Path) -> None:
    cache = KabuTokenCache(tmp_path / "tok.json")
    fetch_called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            fetch_called["n"] += 1
            return httpx.Response(200, json={"Token": "fresh-token"})
        return httpx.Response(200, json={})

    client = _make_client(httpx.MockTransport(handler), token_cache=cache)
    try:
        token = await client.ensure_token()
        assert token == "fresh-token"
        assert fetch_called["n"] == 1
        assert cache.load() == "fresh-token"
    finally:
        await client.aclose()
