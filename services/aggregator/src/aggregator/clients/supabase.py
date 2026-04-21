from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Self

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
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
    """Write-only Supabase client. aggregator only inserts aggregator_logs."""

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
            logger.info("supabase upsert skipped: table=aggregator_logs rows=0")
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
        logger.info("supabase upsert: table=aggregator_logs rows=%d", len(rows))
        return len(rows)
