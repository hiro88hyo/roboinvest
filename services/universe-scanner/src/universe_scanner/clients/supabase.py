from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Self

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class SupabaseError(RuntimeError):
    """Supabase (PostgREST) とのやり取りで発生するエラー。"""


@dataclass(slots=True)
class SupabaseWriter:
    """Supabase PostgREST 経由の書き込み専用クライアント。

    upsert (merge-duplicates) のみを提供する。読み取りは本サービスでは不要。
    """

    url: str
    secret_key: str
    timeout_seconds: float = 30.0
    _client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(
            base_url=self.url.rstrip("/"),
            timeout=self.timeout_seconds,
            headers={
                "apikey": self.secret_key,
                "Authorization": f"Bearer {self.secret_key}",
                "Content-Type": "application/json",
            },
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
    async def upsert(
        self,
        table: str,
        rows: list[dict[str, Any]],
        *,
        on_conflict: str,
    ) -> None:
        """`rows` を `table` に upsert する。`on_conflict` は PK/UNIQUE カラムを CSV で指定。"""
        if not rows:
            logger.info("supabase upsert skipped: table=%s rows=0", table)
            return
        assert self._client is not None
        resp = await self._client.post(
            f"/rest/v1/{table}",
            params={"on_conflict": on_conflict},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=rows,
        )
        if resp.status_code >= 500:
            raise SupabaseError(
                f"transient error: table={table} status={resp.status_code} body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise SupabaseError(
                f"upsert failed: table={table} status={resp.status_code} body={resp.text[:200]}"
            )
        logger.info("supabase upsert: table=%s rows=%d", table, len(rows))
