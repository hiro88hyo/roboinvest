"""Minimal kabu Station client for Gateway risk sizing."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from trade_contracts.kabu_token import KabuTokenCache


class KabuApiError(RuntimeError):
    """Raised when kabu Station returns an unexpected response."""

    def __init__(self, status_code: int, body: dict[str, Any] | str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"kabu API returned HTTP {status_code}: {body!r}")


class KabuWalletClient:
    """Read-only kabu Station wallet client used by Gateway."""

    def __init__(
        self,
        *,
        base_url: str,
        api_password: str,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
        token_cache: KabuTokenCache | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_password = api_password
        self._owns_client = http_client is None
        self._client: httpx.AsyncClient = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._token_cache = token_cache
        self._token: str | None = None

    async def __aenter__(self) -> KabuWalletClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_token(self) -> str:
        response = await self._client.post(
            f"{self._base_url}/token",
            json={"APIPassword": self._api_password},
            headers={"Content-Type": "application/json"},
        )
        body = _check_dict(response)
        token = body.get("Token")
        if not isinstance(token, str) or not token:
            raise KabuApiError(response.status_code, body)
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
        self._token = None
        if self._token_cache is not None:
            self._token_cache.invalidate()

    async def read_stock_account_wallet(self) -> Decimal:
        """Return ``StockAccountWallet`` from ``GET /wallet/cash``."""
        token = await self.ensure_token()
        response = await self._client.get(
            f"{self._base_url}/wallet/cash",
            headers={"X-API-KEY": token},
        )
        if response.status_code in {401, 403}:
            self.invalidate_token()
        body = _check_dict(response)
        try:
            value = Decimal(str(body["StockAccountWallet"]))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise KabuApiError(response.status_code, body) from exc
        if value <= 0:
            raise KabuApiError(response.status_code, body)
        return value


def _decode(response: httpx.Response) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        return {"_raw": response.text}


def _check_dict(response: httpx.Response) -> dict[str, Any]:
    body = _decode(response)
    if response.is_success:
        if not isinstance(body, dict):
            return {"_raw": body}
        return body
    if isinstance(body, dict | str):
        raise KabuApiError(response.status_code, body)
    raise KabuApiError(response.status_code, {"_raw": body})
