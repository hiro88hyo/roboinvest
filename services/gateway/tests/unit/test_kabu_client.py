from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from gateway.clients.kabu import KabuApiError, KabuWalletClient
from trade_contracts.kabu_token import KabuTokenCache


async def test_read_stock_account_wallet_fetches_token_and_parses_wallet(
    tmp_path: Path,
) -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path == "/kabusapi/token":
            return httpx.Response(200, json={"Token": "tok-1"})
        if request.url.path == "/kabusapi/wallet/cash":
            assert request.headers["X-API-KEY"] == "tok-1"
            return httpx.Response(200, json={"StockAccountWallet": 123456.7})
        return httpx.Response(404)

    async with KabuWalletClient(
        base_url="http://localhost/kabusapi",
        api_password="pw",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
        token_cache=KabuTokenCache(tmp_path / "token.json"),
    ) as client:
        wallet = await client.read_stock_account_wallet()

    assert wallet == Decimal("123456.7")
    assert [request.url.path for request in captured] == [
        "/kabusapi/token",
        "/kabusapi/wallet/cash",
    ]


async def test_read_stock_account_wallet_uses_cached_token(tmp_path: Path) -> None:
    cache = KabuTokenCache(tmp_path / "token.json")
    cache.save("cached-token")
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path == "/kabusapi/wallet/cash":
            assert request.headers["X-API-KEY"] == "cached-token"
            return httpx.Response(200, json={"StockAccountWallet": "200000"})
        return httpx.Response(404)

    async with KabuWalletClient(
        base_url="http://localhost/kabusapi",
        api_password="pw",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
        token_cache=cache,
    ) as client:
        wallet = await client.read_stock_account_wallet()

    assert wallet == Decimal("200000")
    assert [request.url.path for request in captured] == ["/kabusapi/wallet/cash"]


async def test_read_stock_account_wallet_invalidates_token_on_auth_error(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "token.json"
    cache = KabuTokenCache(cache_path)
    cache.save("expired-token")

    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"Code": 4001001, "Message": "invalid token"})

    async with KabuWalletClient(
        base_url="http://localhost/kabusapi",
        api_password="pw",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
        token_cache=cache,
    ) as client:
        with pytest.raises(KabuApiError):
            await client.read_stock_account_wallet()

    assert not cache_path.exists()
