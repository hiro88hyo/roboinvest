"""Pub/Sub (REST) subscriber client for OMS Paper.

OMS Paper は 2 本の subscription (paper-orders / raw-market-data) を pull するだけで、
publish はしない (約定結果は Supabase に書き込み)。gateway / aggregator と同じ
synchronous Pull + REST 方針 (streaming Pull は使わない)。
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Self

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class PubSubError(RuntimeError):
    """Pub/Sub (REST API) error wrapper."""


@dataclass(frozen=True, slots=True)
class PulledMessage:
    ack_id: str
    message_id: str
    data: bytes
    attributes: dict[str, str]


def _emulator_base_url(host: str) -> str:
    if "://" in host:
        return host.rstrip("/")
    return f"http://{host}"


@dataclass(slots=True)
class PubSubSubscriber:
    """Pub/Sub REST subscriber. Synchronous Pull in a loop (no streaming Pull)."""

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
        assert self._client is not None
        if max_messages <= 0:
            raise ValueError("max_messages must be positive")
        path = f"/v1/projects/{self.project_id}/subscriptions/{subscription}:pull"
        resp = await self._client.post(
            path,
            json={"maxMessages": max_messages, "returnImmediately": return_immediately},
        )
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
