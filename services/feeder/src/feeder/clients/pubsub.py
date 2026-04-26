"""Pub/Sub (REST) publisher client for Feeder.

Feeder は ``raw-market-data`` トピックに対する **publish のみ** を行う
(購読側は feature-engine / oms-paper)。feature-engine の
``PubSubPublisher`` と同じ REST 方針 (streaming Publish は使わない)。

エミュレータ前提では ``emulator_host`` が指定されたときに認証不要の HTTP
へ繋ぐ。本番 (GCP) 用の Bearer 注入は未対応 (Phase 3 の範囲外)。
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Self

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class PubSubError(RuntimeError):
    """Pub/Sub (REST API) とのやり取りで発生するエラー。"""


def _emulator_base_url(host: str) -> str:
    if "://" in host:
        return host.rstrip("/")
    return f"http://{host}"


@dataclass(slots=True)
class PubSubPublisher:
    """Pub/Sub REST API 経由の publisher。Feeder は publish のみ。

    ``emulator_host`` が空でないときはエミュレータ (認証なし) を前提とする。
    本番利用時は ADC 由来の Bearer トークンをヘッダ注入する必要があるが
    Phase 3 では未対応。
    """

    project_id: str
    emulator_host: str = ""
    timeout_seconds: float = 30.0
    transport: httpx.AsyncBaseTransport | None = None
    _client: httpx.AsyncClient | None = field(default=None, init=False)

    async def __aenter__(self) -> Self:
        if not self.project_id:
            raise PubSubError("project_id must be set")
        base_url = (
            _emulator_base_url(self.emulator_host)
            if self.emulator_host
            else "https://pubsub.googleapis.com"
        )
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=self.timeout_seconds,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
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
        retry=retry_if_exception_type((httpx.HTTPError, PubSubError)),
    )
    async def publish(
        self,
        topic: str,
        *,
        data: bytes,
        attributes: dict[str, str] | None = None,
    ) -> str:
        """1 メッセージをパブリッシュし、採番された messageId を返す。

        tenacity で 3 回まで自動 retry する。失敗時は ``PubSubError`` を投げる。
        """
        assert self._client is not None
        message: dict[str, Any] = {"data": base64.b64encode(data).decode("ascii")}
        if attributes:
            message["attributes"] = attributes
        path = f"/v1/projects/{self.project_id}/topics/{topic}:publish"
        resp = await self._client.post(path, json={"messages": [message]})
        if resp.status_code >= 500:
            raise PubSubError(
                f"transient error: topic={topic} status={resp.status_code} body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise PubSubError(
                f"publish failed: topic={topic} status={resp.status_code} body={resp.text[:200]}"
            )
        payload = resp.json()
        ids = payload.get("messageIds") or []
        if not ids:
            raise PubSubError(f"publish response missing messageIds: body={resp.text[:200]}")
        logger.info("pubsub publish: topic=%s message_id=%s", topic, ids[0])
        return str(ids[0])
