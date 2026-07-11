from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self
from uuid import UUID

import httpx
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from trade_contracts.enums import TradeMode
from trade_contracts.event_paper_dispatch import (
    EventPaperDispatchResult,
    EventPaperDispatchStage,
    canonical_payload_sha256,
)
from trade_contracts.signal import UnifiedTradeSignal

logger = logging.getLogger(__name__)


class SupabaseError(RuntimeError):
    """Supabase (PostgREST) error wrapper."""


def _unified_to_row(signal: UnifiedTradeSignal) -> dict[str, Any]:
    return {
        "signal_id": str(signal.signal_id),
        "symbol": signal.symbol,
        "action": signal.action.value,
        "confidence": signal.confidence,
        "signal_source": signal.signal_source.value,
        "strategy_signal_id_a": (
            str(signal.strategy_signal_id_a) if signal.strategy_signal_id_a else None
        ),
        "strategy_signal_id_b": (
            str(signal.strategy_signal_id_b) if signal.strategy_signal_id_b else None
        ),
        "created_at": signal.created_at.isoformat(),
    }


@dataclass(slots=True)
class SupabaseWriter:
    """Supabase client used by aggregator for logs and lightweight routing state."""

    url: str
    secret_key: str
    timeout_seconds: float = 30.0
    transport: httpx.AsyncBaseTransport | None = None
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
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def insert_aggregator_logs(self, signals: Iterable[UnifiedTradeSignal]) -> int:
        """Insert one row per UnifiedTradeSignal into `aggregator_logs`.

        Uses upsert on `signal_id` so a Pub/Sub redelivery (same signal_id)
        does not produce duplicates. Empty input is a no-op.
        """
        rows = [_unified_to_row(s) for s in signals]
        if not rows:
            logger.debug("supabase upsert skipped: table=aggregator_logs rows=0")
            return 0
        assert self._client is not None
        resp = await self._client.post(
            "/rest/v1/aggregator_logs",
            params={"on_conflict": "signal_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=rows,
        )
        if resp.status_code >= 500:
            raise SupabaseError(
                f"transient error: table=aggregator_logs status={resp.status_code} "
                f"body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise SupabaseError(
                f"insert failed: table=aggregator_logs status={resp.status_code} "
                f"body={resp.text[:200]}"
            )
        logger.debug("supabase upsert: table=aggregator_logs rows=%d", len(rows))
        return len(rows)

    async def read_event_paper_dispatch(
        self,
        *,
        stage: EventPaperDispatchStage,
        input_signal_id: UUID,
    ) -> EventPaperDispatchResult | None:
        """Read a durable event-paper delivery state, if one exists."""

        return await self._event_paper_stage_dispatch(
            action="read",
            stage=stage,
            input_signal_id=input_signal_id,
            allow_missing=True,
        )

    async def prepare_event_paper_dispatch(
        self,
        *,
        stage: EventPaperDispatchStage,
        input_signal_id: UUID,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any],
        destination_topic: str,
    ) -> EventPaperDispatchResult:
        """Persist immutable event-paper input/output before any publish attempt."""

        result = await self._event_paper_stage_dispatch(
            action="prepare",
            stage=stage,
            input_signal_id=input_signal_id,
            input_payload=input_payload,
            input_payload_sha256=canonical_payload_sha256(input_payload),
            output_payload=output_payload,
            output_payload_sha256=canonical_payload_sha256(output_payload),
            destination_topic=destination_topic,
            allow_missing=False,
        )
        assert result is not None
        return result

    async def begin_event_paper_dispatch(
        self,
        *,
        stage: EventPaperDispatchStage,
        input_signal_id: UUID,
        attempt_id: str,
        attempted_at: datetime,
    ) -> EventPaperDispatchResult:
        """Durably cross the point of no return before publishing to Pub/Sub."""

        result = await self._event_paper_stage_dispatch(
            action="begin",
            stage=stage,
            input_signal_id=input_signal_id,
            attempt_id=attempt_id,
            occurred_at=attempted_at,
            allow_missing=False,
        )
        assert result is not None
        return result

    async def confirm_event_paper_dispatch(
        self,
        *,
        stage: EventPaperDispatchStage,
        input_signal_id: UUID,
        attempt_id: str,
        pubsub_message_id: str,
        confirmed_at: datetime,
    ) -> EventPaperDispatchResult:
        """Checkpoint a successful Pub/Sub publish for the durable receipt."""

        result = await self._event_paper_stage_dispatch(
            action="confirm",
            stage=stage,
            input_signal_id=input_signal_id,
            attempt_id=attempt_id,
            pubsub_message_id=pubsub_message_id,
            occurred_at=confirmed_at,
            allow_missing=False,
        )
        assert result is not None
        return result

    async def mark_event_paper_dispatch_ambiguous(
        self,
        *,
        stage: EventPaperDispatchStage,
        input_signal_id: UUID,
        attempt_id: str,
        occurred_at: datetime,
        error: str,
    ) -> EventPaperDispatchResult:
        """Make an uncheckpointed external attempt terminal for automatic workers."""

        result = await self._event_paper_stage_dispatch(
            action="ambiguous",
            stage=stage,
            input_signal_id=input_signal_id,
            attempt_id=attempt_id,
            occurred_at=occurred_at,
            error=error,
            allow_missing=False,
        )
        assert result is not None
        return result

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def _event_paper_stage_dispatch(
        self,
        *,
        action: str,
        stage: EventPaperDispatchStage,
        input_signal_id: UUID,
        input_payload: dict[str, Any] | None = None,
        input_payload_sha256: str | None = None,
        output_payload: dict[str, Any] | None = None,
        output_payload_sha256: str | None = None,
        destination_topic: str | None = None,
        attempt_id: str | None = None,
        pubsub_message_id: str | None = None,
        occurred_at: datetime | None = None,
        error: str | None = None,
        allow_missing: bool,
    ) -> EventPaperDispatchResult | None:
        assert self._client is not None
        response = await self._client.post(
            "/rest/v1/rpc/event_paper_stage_dispatch",
            json={
                "p_action": action,
                "p_stage": stage.value,
                "p_input_signal_id": str(input_signal_id),
                "p_input_payload": input_payload,
                "p_input_payload_sha256": input_payload_sha256,
                "p_output_payload": output_payload,
                "p_output_payload_sha256": output_payload_sha256,
                "p_destination_topic": destination_topic,
                "p_attempt_id": attempt_id,
                "p_pubsub_message_id": pubsub_message_id,
                "p_occurred_at": occurred_at.isoformat() if occurred_at is not None else None,
                "p_error": error,
            },
        )
        if response.status_code >= 500:
            raise SupabaseError(
                "transient error: rpc=event_paper_stage_dispatch "
                f"status={response.status_code} body={response.text[:200]}"
            )
        if response.status_code >= 300:
            raise SupabaseError(
                "rpc failed: rpc=event_paper_stage_dispatch "
                f"status={response.status_code} body={response.text[:200]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SupabaseError("invalid event_paper_stage_dispatch response JSON") from exc
        if not isinstance(payload, list):
            raise SupabaseError("invalid event_paper_stage_dispatch response: expected a list")
        if not payload and allow_missing:
            return None
        if len(payload) != 1 or not isinstance(payload[0], dict):
            raise SupabaseError("invalid event_paper_stage_dispatch response row count")
        try:
            return EventPaperDispatchResult.model_validate(payload[0])
        except ValidationError as exc:
            raise SupabaseError("invalid event_paper_stage_dispatch response") from exc

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def read_trade_mode(self) -> TradeMode:
        """Return the current production trade mode from system_status."""
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/system_status",
            params={"select": "trade_mode", "id": "eq.1", "limit": "1"},
        )
        if resp.status_code >= 500:
            raise SupabaseError(
                f"transient error: table=system_status status={resp.status_code} "
                f"body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise SupabaseError(
                f"read failed: table=system_status status={resp.status_code} body={resp.text[:200]}"
            )
        rows = resp.json()
        if not rows:
            raise SupabaseError("system_status row id=1 not found")
        return TradeMode(str(rows[0]["trade_mode"]).lower())

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def read_long_quantity(self, *, symbol: str, trade_mode: TradeMode) -> int:
        """Return summed LONG quantity for the symbol in the active trade mode."""
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/positions",
            params={
                "select": "quantity",
                "symbol": f"eq.{symbol}",
                "trade_type": f"eq.{trade_mode.value}",
                "side": "eq.LONG",
            },
        )
        if resp.status_code >= 500:
            raise SupabaseError(
                f"transient error: table=positions status={resp.status_code} body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise SupabaseError(
                f"read failed: table=positions status={resp.status_code} body={resp.text[:200]}"
            )
        rows = resp.json()
        total = 0
        for row in rows:
            try:
                total += int(row["quantity"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SupabaseError(f"invalid position quantity row: {row!r}") from exc
        return total
