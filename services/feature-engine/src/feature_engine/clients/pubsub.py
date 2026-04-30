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


@dataclass(frozen=True, slots=True)
class PulledMessage:
    """`subscriptions:pull` の 1 メッセージ分。"""

    ack_id: str
    message_id: str
    data: bytes
    attributes: dict[str, str]


def _emulator_base_url(host: str) -> str:
    if "://" in host:
        return host.rstrip("/")
    return f"http://{host}"


@dataclass(slots=True)
class PubSubPublisher:
    """Pub/Sub REST API 経由の publisher。

    `emulator_host` が空でないときはエミュレータ (認証なし) を前提とする。
    本番利用時は ADC 由来の Bearer トークンをヘッダ注入する必要があるが現状は未対応。
    """

    project_id: str
    emulator_host: str = ""
    timeout_seconds: float = 30.0
    transport: httpx.AsyncBaseTransport | None = None
    _client: httpx.AsyncClient | None = None

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

        複数メッセージのバッチ publish は上位で組み立てる想定 (ここでは扱わない)。
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


@dataclass(slots=True)
class PubSubSubscriber:
    """Pub/Sub REST API 経由の subscriber。

    `pull` / `acknowledge` / `modify_ack_deadline` を薄くラップする。
    ストリーミング Pull は使わず、短いデッドラインの同期 Pull をループで回す前提。
    """

    project_id: str
    emulator_host: str = ""
    timeout_seconds: float = 60.0
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
    async def pull(
        self,
        subscription: str,
        *,
        max_messages: int,
        return_immediately: bool = False,
    ) -> list[PulledMessage]:
        """`subscription` からメッセージを最大 `max_messages` 件受信する。

        `return_immediately=True` で「すぐ返す」モード (なければ空配列)。
        False のときはサーバ側が長めに待って配信する (long-poll 相当)。
        """
        assert self._client is not None
        if max_messages <= 0:
            raise ValueError("max_messages must be positive")
        path = f"/v1/projects/{self.project_id}/subscriptions/{subscription}:pull"
        try:
            resp = await self._client.post(
                path,
                json={"maxMessages": max_messages, "returnImmediately": return_immediately},
            )
        except httpx.ReadTimeout:
            # long-poll idle timeout — emulator が deadline 内にメッセージを返さなかった。
            # アイドル状態として正常扱いし、空配列を返してループ継続させる。
            logger.debug("pubsub pull: sub=%s read timeout (idle)", subscription)
            return []
        if resp.status_code >= 500:
            raise PubSubError(
                f"transient error: sub={subscription} status={resp.status_code} "
                f"body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise PubSubError(
                f"pull failed: sub={subscription} status={resp.status_code} body={resp.text[:200]}"
            )
        body = resp.json()
        raw = body.get("receivedMessages") or []
        out: list[PulledMessage] = []
        for rm in raw:
            msg = rm.get("message") or {}
            data_b64 = msg.get("data") or ""
            out.append(
                PulledMessage(
                    ack_id=str(rm["ackId"]),
                    message_id=str(msg.get("messageId", "")),
                    data=base64.b64decode(data_b64) if data_b64 else b"",
                    attributes=dict(msg.get("attributes") or {}),
                )
            )
        if out:
            logger.info("pubsub pull: sub=%s received=%d", subscription, len(out))
        return out

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, PubSubError)),
    )
    async def acknowledge(self, subscription: str, ack_ids: list[str]) -> None:
        """受信済みメッセージを ack する。空リストは no-op。"""
        if not ack_ids:
            return
        assert self._client is not None
        path = f"/v1/projects/{self.project_id}/subscriptions/{subscription}:acknowledge"
        resp = await self._client.post(path, json={"ackIds": ack_ids})
        if resp.status_code >= 500:
            raise PubSubError(
                f"transient error: sub={subscription} status={resp.status_code} "
                f"body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise PubSubError(
                f"ack failed: sub={subscription} status={resp.status_code} body={resp.text[:200]}"
            )
        logger.info("pubsub ack: sub=%s count=%d", subscription, len(ack_ids))

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, PubSubError)),
    )
    async def modify_ack_deadline(
        self,
        subscription: str,
        ack_ids: list[str],
        *,
        deadline_seconds: int,
    ) -> None:
        """処理中メッセージの ack 期限を延長する (処理が長引いた場合に再配信を防ぐ)。"""
        if not ack_ids:
            return
        assert self._client is not None
        path = f"/v1/projects/{self.project_id}/subscriptions/{subscription}:modifyAckDeadline"
        resp = await self._client.post(
            path,
            json={"ackIds": ack_ids, "ackDeadlineSeconds": deadline_seconds},
        )
        if resp.status_code >= 500:
            raise PubSubError(
                f"transient error: sub={subscription} status={resp.status_code} "
                f"body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise PubSubError(
                f"modify_ack_deadline failed: sub={subscription} "
                f"status={resp.status_code} body={resp.text[:200]}"
            )
