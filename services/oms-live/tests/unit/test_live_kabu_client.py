"""KabuLiveClient の単体テスト。httpx の MockTransport で REST 系を検証する。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from oms_live.kabu_client import KabuApiError, KabuLiveClient
from trade_contracts.kabu_token import KabuTokenCache


def _make_client(
    handler: httpx.MockTransport,
    token_cache: KabuTokenCache | None = None,
) -> KabuLiveClient:
    return KabuLiveClient(
        base_url="http://localhost:18081/kabusapi",
        api_password="dummy-pw",
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


async def test_invalidate_token_forces_refetch() -> None:
    counter = {"token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            counter["token"] += 1
            return httpx.Response(200, json={"Token": f"tok-{counter['token']}"})
        return httpx.Response(200, json={"Result": 0, "OrderId": "1"})

    client = _make_client(httpx.MockTransport(handler))
    try:
        first = await client.ensure_token()
        client.invalidate_token()
        second = await client.ensure_token()
        assert first != second
        assert counter["token"] == 2
    finally:
        await client.aclose()


async def test_send_order_posts_payload_with_token_header() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"Token": "tok-1"})
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("X-API-KEY")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"Result": 0, "OrderId": "20260429000001"})

    client = _make_client(httpx.MockTransport(handler))
    try:
        result = await client.send_order(
            {
                "Symbol": "7203",
                "Exchange": 1,
                "Side": "2",
                "Qty": 100,
                "FrontOrderType": 10,
                "Price": 0,
                "Password": "order-pw",
            }
        )
        assert result == {"Result": 0, "OrderId": "20260429000001"}
        assert captured["method"] == "POST"
        assert captured["url"] == "http://localhost:18081/kabusapi/sendorder"
        assert captured["api_key"] == "tok-1"
        assert captured["body"] == {
            "Symbol": "7203",
            "Exchange": 1,
            "Side": "2",
            "Qty": 100,
            "FrontOrderType": 10,
            "Price": 0,
            "Password": "order-pw",
        }
    finally:
        await client.aclose()


async def test_send_order_propagates_4xx_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"Token": "tok-1"})
        return httpx.Response(
            400,
            json={"Code": 4002005, "Message": "注文パスワードが正しくありません"},
        )

    client = _make_client(httpx.MockTransport(handler))
    try:
        with pytest.raises(KabuApiError) as exc:
            await client.send_order({"foo": "bar"})
        assert exc.value.status_code == 400
        assert exc.value.body == {"Code": 4002005, "Message": "注文パスワードが正しくありません"}
    finally:
        await client.aclose()


async def test_cancel_order_puts_order_id_and_password() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"Token": "tok-1"})
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"Result": 0, "OrderId": "20260429000001"})

    client = _make_client(httpx.MockTransport(handler))
    try:
        result = await client.cancel_order(order_id="20260429000001", password="order-pw")
        assert result == {"Result": 0, "OrderId": "20260429000001"}
        assert captured["method"] == "PUT"
        assert captured["url"] == "http://localhost:18081/kabusapi/cancelorder"
        assert captured["body"] == {"OrderId": "20260429000001", "Password": "order-pw"}
    finally:
        await client.aclose()


async def test_get_order_returns_first_element_of_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"Token": "tok-1"})
        assert request.url.path.endswith("/orders")
        assert dict(request.url.params) == {"id": "20260429000001"}
        return httpx.Response(
            200,
            json=[
                {
                    "ID": "20260429000001",
                    "Symbol": "7203",
                    "Side": "2",
                    "OrderQty": 100,
                    "CumQty": 100,
                    "State": 3,
                    "OrderState": 3,
                    "Details": [],
                }
            ],
        )

    client = _make_client(httpx.MockTransport(handler))
    try:
        order = await client.get_order("20260429000001")
        assert order["ID"] == "20260429000001"
        assert order["State"] == 3
    finally:
        await client.aclose()


async def test_get_order_raises_when_empty_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"Token": "tok-1"})
        return httpx.Response(200, json=[])

    client = _make_client(httpx.MockTransport(handler))
    try:
        with pytest.raises(KabuApiError) as exc:
            await client.get_order("nonexistent")
        assert exc.value.status_code == 404
    finally:
        await client.aclose()


async def test_list_positions_passes_filters() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"Token": "tok-1"})
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json=[
                {"Symbol": "7203", "LeavesQty": 100, "Price": 1000.0},
            ],
        )

    client = _make_client(httpx.MockTransport(handler))
    try:
        positions = await client.list_positions(product=1, symbol="7203")
        assert len(positions) == 1
        assert captured["params"] == {"product": "1", "symbol": "7203"}
    finally:
        await client.aclose()


async def test_list_positions_no_filter_sends_no_params() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"Token": "tok-1"})
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=[])

    client = _make_client(httpx.MockTransport(handler))
    try:
        await client.list_positions()
        assert captured["params"] == {}
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
    finally:
        await client.aclose()


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
