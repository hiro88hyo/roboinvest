from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Self

import httpx
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.auth.credentials import AnonymousCredentials
from google.cloud import pubsub_v1
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class PubSubError(RuntimeError):
    """Pub/Sub client error wrapper."""


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


def _emulator_api_endpoint(host: str) -> str:
    if "://" in host:
        return host.split("://", 1)[1].rstrip("/")
    return host.rstrip("/")


@dataclass(slots=True)
class PubSubPublisher:
    """Async-shaped publisher backed by google-cloud-pubsub.

    The public API intentionally matches the previous REST wrapper used by
    services. Passing `transport` keeps the old REST path for unit tests that
    use httpx.MockTransport.
    """

    project_id: str
    emulator_host: str = ""
    timeout_seconds: float = 30.0
    transport: httpx.AsyncBaseTransport | None = None
    _client: pubsub_v1.PublisherClient | httpx.AsyncClient | None = field(default=None, init=False)

    async def __aenter__(self) -> Self:
        if not self.project_id:
            raise PubSubError("project_id must be set")
        if self.transport is not None:
            base_url = (
                _emulator_base_url(self.emulator_host)
                if self.emulator_host
                else "https://pubsub.googleapis.com"
            )
            self._client = httpx.AsyncClient(
                base_url=base_url,
                timeout=self.timeout_seconds,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                transport=self.transport,
            )
        else:
            if self.emulator_host:
                self._client = pubsub_v1.PublisherClient(
                    client_options={"api_endpoint": _emulator_api_endpoint(self.emulator_host)},
                    credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
                )
            else:
                self._client = pubsub_v1.PublisherClient()
        return self

    async def __aexit__(self, *exc: object) -> None:
        client = self._client
        if isinstance(client, httpx.AsyncClient):
            await client.aclose()
        elif isinstance(client, pubsub_v1.PublisherClient):
            client.transport.close()
        self._client = None

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (GoogleAPICallError, RetryError, httpx.HTTPError, PubSubError)
        ),
    )
    async def publish(
        self,
        topic: str,
        *,
        data: bytes,
        attributes: dict[str, str] | None = None,
    ) -> str:
        client = self._client
        if client is None:
            raise PubSubError("publisher is not started")
        if isinstance(client, httpx.AsyncClient):
            return await self._publish_rest(client, topic, data=data, attributes=attributes)
        topic_path = client.topic_path(self.project_id, topic)
        future = client.publish(topic_path, data, **(attributes or {}))
        message_id = await asyncio.to_thread(future.result, timeout=self.timeout_seconds)
        logger.debug("pubsub publish: topic=%s message_id=%s", topic, message_id)
        return str(message_id)

    async def _publish_rest(
        self,
        client: httpx.AsyncClient,
        topic: str,
        *,
        data: bytes,
        attributes: dict[str, str] | None,
    ) -> str:
        message: dict[str, Any] = {"data": base64.b64encode(data).decode("ascii")}
        if attributes:
            message["attributes"] = attributes
        path = f"/v1/projects/{self.project_id}/topics/{topic}:publish"
        resp = await client.post(path, json={"messages": [message]})
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
        logger.debug("pubsub publish: topic=%s message_id=%s", topic, ids[0])
        return str(ids[0])


@dataclass(slots=True)
class PubSubSubscriber:
    """Async-shaped pull subscriber backed by google-cloud-pubsub."""

    project_id: str
    emulator_host: str = ""
    timeout_seconds: float = 60.0
    transport: httpx.AsyncBaseTransport | None = None
    _client: pubsub_v1.SubscriberClient | httpx.AsyncClient | None = field(default=None, init=False)

    async def __aenter__(self) -> Self:
        if not self.project_id:
            raise PubSubError("project_id must be set")
        if self.transport is not None:
            base_url = (
                _emulator_base_url(self.emulator_host)
                if self.emulator_host
                else "https://pubsub.googleapis.com"
            )
            self._client = httpx.AsyncClient(
                base_url=base_url,
                timeout=self.timeout_seconds,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                transport=self.transport,
            )
        else:
            if self.emulator_host:
                self._client = pubsub_v1.SubscriberClient(
                    client_options={"api_endpoint": _emulator_api_endpoint(self.emulator_host)},
                    credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
                )
            else:
                self._client = pubsub_v1.SubscriberClient()
        return self

    async def __aexit__(self, *exc: object) -> None:
        client = self._client
        if isinstance(client, httpx.AsyncClient):
            await client.aclose()
        elif isinstance(client, pubsub_v1.SubscriberClient):
            client.close()
        self._client = None

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (GoogleAPICallError, RetryError, httpx.HTTPError, PubSubError)
        ),
    )
    async def pull(
        self,
        subscription: str,
        *,
        max_messages: int,
        return_immediately: bool = False,
    ) -> list[PulledMessage]:
        if max_messages <= 0:
            raise ValueError("max_messages must be positive")
        client = self._client
        if client is None:
            raise PubSubError("subscriber is not started")
        if isinstance(client, httpx.AsyncClient):
            return await self._pull_rest(
                client,
                subscription,
                max_messages=max_messages,
                return_immediately=return_immediately,
            )
        subscription_path = client.subscription_path(self.project_id, subscription)
        try:
            response = await asyncio.to_thread(
                client.pull,
                request={"subscription": subscription_path, "max_messages": max_messages},
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            logger.debug("pubsub pull: sub=%s timeout (idle)", subscription)
            return []
        out = [
            PulledMessage(
                ack_id=received.ack_id,
                message_id=received.message.message_id,
                data=bytes(received.message.data),
                attributes=dict(received.message.attributes),
            )
            for received in response.received_messages
        ]
        if out:
            logger.debug("pubsub pull: sub=%s received=%d", subscription, len(out))
        return out

    async def _pull_rest(
        self,
        client: httpx.AsyncClient,
        subscription: str,
        *,
        max_messages: int,
        return_immediately: bool,
    ) -> list[PulledMessage]:
        path = f"/v1/projects/{self.project_id}/subscriptions/{subscription}:pull"
        try:
            resp = await client.post(
                path,
                json={"maxMessages": max_messages, "returnImmediately": return_immediately},
            )
        except httpx.ReadTimeout:
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
        raw = resp.json().get("receivedMessages") or []
        out = []
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
            logger.debug("pubsub pull: sub=%s received=%d", subscription, len(out))
        return out

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (GoogleAPICallError, RetryError, httpx.HTTPError, PubSubError)
        ),
    )
    async def acknowledge(self, subscription: str, ack_ids: list[str]) -> None:
        if not ack_ids:
            return
        client = self._client
        if client is None:
            raise PubSubError("subscriber is not started")
        if isinstance(client, httpx.AsyncClient):
            await self._acknowledge_rest(client, subscription, ack_ids)
        else:
            subscription_path = client.subscription_path(self.project_id, subscription)
            await asyncio.to_thread(
                client.acknowledge,
                request={"subscription": subscription_path, "ack_ids": ack_ids},
                timeout=self.timeout_seconds,
            )
        logger.debug("pubsub ack: sub=%s count=%d", subscription, len(ack_ids))

    async def _acknowledge_rest(
        self, client: httpx.AsyncClient, subscription: str, ack_ids: list[str]
    ) -> None:
        path = f"/v1/projects/{self.project_id}/subscriptions/{subscription}:acknowledge"
        resp = await client.post(path, json={"ackIds": ack_ids})
        if resp.status_code >= 500:
            raise PubSubError(
                f"transient error: sub={subscription} status={resp.status_code} "
                f"body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise PubSubError(
                f"ack failed: sub={subscription} status={resp.status_code} body={resp.text[:200]}"
            )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (GoogleAPICallError, RetryError, httpx.HTTPError, PubSubError)
        ),
    )
    async def modify_ack_deadline(
        self,
        subscription: str,
        ack_ids: list[str],
        *,
        deadline_seconds: int,
    ) -> None:
        if not ack_ids:
            return
        client = self._client
        if client is None:
            raise PubSubError("subscriber is not started")
        if isinstance(client, httpx.AsyncClient):
            await self._modify_ack_deadline_rest(
                client, subscription, ack_ids, deadline_seconds=deadline_seconds
            )
        else:
            subscription_path = client.subscription_path(self.project_id, subscription)
            await asyncio.to_thread(
                client.modify_ack_deadline,
                request={
                    "subscription": subscription_path,
                    "ack_ids": ack_ids,
                    "ack_deadline_seconds": deadline_seconds,
                },
                timeout=self.timeout_seconds,
            )
        logger.debug(
            "pubsub modify_ack_deadline: sub=%s count=%d deadline=%s",
            subscription,
            len(ack_ids),
            deadline_seconds,
        )

    async def _modify_ack_deadline_rest(
        self,
        client: httpx.AsyncClient,
        subscription: str,
        ack_ids: list[str],
        *,
        deadline_seconds: int,
    ) -> None:
        path = f"/v1/projects/{self.project_id}/subscriptions/{subscription}:modifyAckDeadline"
        resp = await client.post(
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
