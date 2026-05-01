"""Supabase (PostgREST) client for the gateway.

Gateway needs three operations:
  * read the singleton ``system_status`` row into a ``KillSwitchState``
  * read the existing LONG quantity for a symbol + trade_type from ``positions``
  * flip ``system_status.is_trading_allowed = false`` when a kill-switch limit
    has been breached (fail-closed)

The client is pure REST (no Supabase SDK). PostgREST returns JSON arrays — we
parse defensively and raise ``SupabaseError`` on unexpected shapes so the
streaming runner can treat them as fatal and let Pub/Sub redeliver.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Self

import httpx
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from trade_contracts.enums import TradeMode
from trade_contracts.risk import KillSwitchState

logger = logging.getLogger(__name__)


class SupabaseError(RuntimeError):
    """Supabase (PostgREST) error wrapper."""


@dataclass(slots=True)
class SupabaseClient:
    """Read + narrow-write Supabase client used by the gateway streaming loop."""

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
    async def read_system_status(self) -> KillSwitchState:
        """Fetch the ``id=1`` row from ``system_status`` and parse to KillSwitchState."""
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/system_status",
            params={"select": "*", "id": "eq.1", "limit": "1"},
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
        payload = resp.json()
        if not isinstance(payload, list) or not payload:
            raise SupabaseError("system_status row (id=1) not found")
        row = payload[0]
        try:
            return KillSwitchState.model_validate(row)
        except ValidationError as exc:
            raise SupabaseError(f"invalid system_status row: {exc}") from exc

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def read_long_quantity(self, *, symbol: str, trade_mode: TradeMode) -> int:
        """Return the existing LONG quantity for ``(symbol, trade_mode)``, or 0 if none."""
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/positions",
            params={
                "select": "quantity,side",
                "symbol": f"eq.{symbol}",
                "trade_type": f"eq.{trade_mode.value}",
                "limit": "1",
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
        if not isinstance(rows, list) or not rows:
            return 0
        row: dict[str, Any] = rows[0]
        # side は LONG 固定 (現物のみ) だが、将来の防衛的チェック
        if str(row.get("side", "LONG")) != "LONG":
            raise SupabaseError(
                f"unexpected non-LONG position: symbol={symbol} side={row.get('side')}"
            )
        try:
            return int(row["quantity"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SupabaseError(f"invalid position quantity: {row}") from exc

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def read_latest_price(self, *, symbol: str) -> Decimal | None:
        """Return the latest known price for ``symbol`` from ``positions.current_price``.

        Feature Engine updates ``positions.current_price`` on every tick for symbols
        that have an open position (live or paper). Gateway uses this as the entry
        price for BUY lot calculation. Returns ``None`` if no position row exists
        for the symbol under any trade_type — streaming runner will reject such
        BUY signals with ``missing_entry_price`` (fail-closed).
        """
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/positions",
            params={
                "select": "current_price",
                "symbol": f"eq.{symbol}",
                "order": "opened_at.desc",
                "limit": "1",
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
        if not isinstance(rows, list) or not rows:
            return None
        raw = rows[0].get("current_price")
        if raw is None:
            return None
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError) as exc:
            raise SupabaseError(f"invalid current_price: {raw!r}") from exc

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def read_latest_daily_close(self, *, symbol: str) -> Decimal | None:
        """Return the latest known daily close for ``symbol`` from ``daily_ohlcv``.

        Used as a paper-mode fallback when ``positions.current_price`` is
        unavailable (no open position yet) — Universe Scanner refreshes
        ``daily_ohlcv`` daily, so the most recent close is a reasonable proxy
        for entry price in BUY lot calculation. Live mode does NOT fall back
        here (fail-closed).
        """
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/daily_ohlcv",
            params={
                "select": "close",
                "symbol": f"eq.{symbol}",
                "order": "date.desc",
                "limit": "1",
            },
        )
        if resp.status_code >= 500:
            raise SupabaseError(
                f"transient error: table=daily_ohlcv status={resp.status_code} "
                f"body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise SupabaseError(
                f"read failed: table=daily_ohlcv status={resp.status_code} body={resp.text[:200]}"
            )
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            return None
        raw = rows[0].get("close")
        if raw is None:
            return None
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError) as exc:
            raise SupabaseError(f"invalid daily_ohlcv close: {raw!r}") from exc

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def disable_trading(self, *, now: datetime | None = None) -> None:
        """Flip ``system_status.is_trading_allowed`` to ``false`` (kill-switch firing).

        Idempotent — safe to call repeatedly because Pub/Sub can redeliver the
        same signal after a crash.
        """
        assert self._client is not None
        stamp = (now or datetime.now(UTC)).isoformat()
        resp = await self._client.patch(
            "/rest/v1/system_status",
            params={"id": "eq.1"},
            headers={"Prefer": "return=minimal"},
            json={"is_trading_allowed": False, "updated_at": stamp},
        )
        if resp.status_code >= 500:
            raise SupabaseError(
                f"transient error: table=system_status status={resp.status_code} "
                f"body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise SupabaseError(
                f"update failed: table=system_status status={resp.status_code} "
                f"body={resp.text[:200]}"
            )
        logger.info("supabase update: table=system_status is_trading_allowed=false")
