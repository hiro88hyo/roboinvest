from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Self

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class JQuantsError(RuntimeError):
    """J-Quants API とのやり取りで発生するエラー。"""


class JQuantsApiVersion(StrEnum):
    V1 = "v1"
    V2 = "v2"


def _fmt_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")


@dataclass(slots=True)
class JQuantsClient:
    """J-Quants API クライアント (async)。

    - v2 は API key (`x-api-key`) を使用
    - v1 は `auth_refresh` で ID トークンを取得し、以降のリクエストに使用
    - 429 / 5xx は指数バックオフでリトライ
    - ページング (`pagination_key`) は自動で解決
    """

    refresh_token: str | None = None
    api_key: str | None = None
    api_version: JQuantsApiVersion = JQuantsApiVersion.V2
    base_url: str = "https://api.jquants.com/v2"
    timeout_seconds: float = 30.0
    _client: httpx.AsyncClient | None = None
    _id_token: str | None = None

    async def __aenter__(self) -> Self:
        if self.api_version == JQuantsApiVersion.V2 and not self.api_key:
            raise JQuantsError("J-Quants v2 requires api_key")
        if self.api_version == JQuantsApiVersion.V1 and not self.refresh_token:
            raise JQuantsError("J-Quants v1 requires refresh_token")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_seconds)
        if self.api_version == JQuantsApiVersion.V1:
            await self._authenticate_v1()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _authenticate_v1(self) -> None:
        if not self.refresh_token:
            raise JQuantsError("J-Quants v1 requires refresh_token")
        assert self._client is not None
        resp = await self._client.post(
            "/token/auth_refresh",
            params={"refreshtoken": self.refresh_token},
        )
        if resp.status_code != 200:
            raise JQuantsError(
                f"auth_refresh failed: status={resp.status_code} body={resp.text[:200]}"
            )
        body = resp.json()
        id_token = body.get("idToken")
        if not id_token:
            raise JQuantsError("auth_refresh response missing idToken")
        self._id_token = id_token
        logger.info("J-Quants: authenticated")

    @retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        retry=retry_if_exception_type((httpx.HTTPError, JQuantsError)),
    )
    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self._client is not None
        headers: dict[str, str]
        if self.api_version == JQuantsApiVersion.V1:
            assert self._id_token is not None
            headers = {"Authorization": f"Bearer {self._id_token}"}
        else:
            assert self.api_key is not None
            headers = {"x-api-key": self.api_key}
        resp = await self._client.get(
            path,
            params=params,
            headers=headers,
        )
        if resp.status_code == 429 or resp.status_code >= 500:
            raise JQuantsError(f"transient error: status={resp.status_code}")
        if resp.status_code != 200:
            raise JQuantsError(f"{path} failed: status={resp.status_code} body={resp.text[:200]}")
        body: dict[str, Any] = resp.json()
        return body

    async def _paginate(self, path: str, params: dict[str, Any], key: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        next_key: str | None = None
        while True:
            page_params = dict(params)
            if next_key is not None:
                page_params["pagination_key"] = next_key
            body = await self._get(path, page_params)
            out.extend(body.get(key, []))
            next_key = body.get("pagination_key")
            if not next_key:
                break
        return out

    async def listed_info(self, as_of: date | None = None) -> list[dict[str, Any]]:
        """全上場銘柄の情報を返す。"""
        params: dict[str, Any] = {}
        if as_of is not None:
            params["date"] = _fmt_date(as_of)
        if self.api_version == JQuantsApiVersion.V1:
            return await self._paginate("/listed/info", params, key="info")
        return await self._paginate("/equities/master", params, key="data")

    async def daily_quotes(
        self,
        *,
        target_date: date | None = None,
        code: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """日次 OHLCV を返す。`target_date` 指定で当該日全銘柄、`code`+`from/to` で単銘柄期間。"""
        params: dict[str, Any] = {}
        if target_date is not None:
            params["date"] = _fmt_date(target_date)
        if code is not None:
            params["code"] = code
        if from_date is not None:
            params["from"] = _fmt_date(from_date)
        if to_date is not None:
            params["to"] = _fmt_date(to_date)
        if self.api_version == JQuantsApiVersion.V1:
            return await self._paginate("/prices/daily_quotes", params, key="daily_quotes")
        return await self._paginate("/equities/bars/daily", params, key="data")
