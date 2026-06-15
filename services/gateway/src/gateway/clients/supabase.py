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
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Self
from uuid import UUID

import httpx
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from trade_contracts.enums import TradeMode
from trade_contracts.risk import KillSwitchState

logger = logging.getLogger(__name__)


class SupabaseError(RuntimeError):
    """Supabase (PostgREST) error wrapper."""


_KILL_SWITCH_STATE_FIELDS = frozenset(
    {
        "id",
        "is_trading_allowed",
        "trade_mode",
        "trading_style",
        "daily_pnl",
        "weekly_pnl",
        "monthly_pnl",
        "daily_loss_limit",
        "weekly_loss_limit",
        "monthly_loss_limit",
        "updated_at",
    }
)


@dataclass(frozen=True, slots=True)
class MarketRegimeState:
    valid_date: date
    regime: str
    confidence: Decimal
    buy_enabled: bool
    position_size_multiplier: Decimal
    source: str
    rationale: list[Any]
    metrics: dict[str, Any]
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class KillSwitchDecision:
    state: KillSwitchState
    passed: bool
    reason: str | None
    disabled: bool


@dataclass(frozen=True, slots=True)
class RiskReservationDecision:
    passed: bool
    reason: str | None
    reserved: bool
    active_risk_before: Decimal
    active_risk_after: Decimal
    daily_pnl: Decimal
    daily_loss_limit: Decimal
    weekly_pnl: Decimal
    weekly_loss_limit: Decimal
    monthly_pnl: Decimal
    monthly_loss_limit: Decimal


@dataclass(frozen=True, slots=True)
class DailyLiquiditySnapshot:
    close: Decimal
    volume: int
    turnover: Decimal


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
    async def check_kill_switch(self) -> KillSwitchDecision:
        """Atomically read, evaluate, and possibly disable the gateway kill switch.

        The database function locks the singleton ``system_status`` row while it
        evaluates live-mode PnL limits and flips ``is_trading_allowed=false``.
        This avoids the streaming runner's previous read-then-patch race.
        """
        assert self._client is not None
        resp = await self._client.post("/rest/v1/rpc/gateway_check_kill_switch", json={})
        if resp.status_code >= 500:
            raise SupabaseError(
                f"transient error: rpc=gateway_check_kill_switch status={resp.status_code} "
                f"body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise SupabaseError(
                f"rpc failed: rpc=gateway_check_kill_switch status={resp.status_code} "
                f"body={resp.text[:200]}"
            )
        row = _single_rpc_row(resp.json(), rpc="gateway_check_kill_switch")

        state_payload = {k: v for k, v in row.items() if k in _KILL_SWITCH_STATE_FIELDS}
        try:
            state = KillSwitchState.model_validate(state_payload)
        except ValidationError as exc:
            raise SupabaseError(f"invalid gateway_check_kill_switch row: {exc}") from exc

        return KillSwitchDecision(
            state=state,
            passed=_parse_rpc_bool(row.get("passed"), field="passed"),
            reason=_parse_optional_str(row.get("reason"), field="reason"),
            disabled=_parse_rpc_bool(row.get("disabled"), field="disabled"),
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def reserve_order_risk(
        self,
        *,
        order_id: UUID,
        trade_mode: TradeMode,
        trading_date: date,
        symbol: str,
        side: str,
        risk_amount: Decimal,
        notional_amount: Decimal,
    ) -> RiskReservationDecision:
        """Atomically reserve worst-case risk for an approved live BUY order."""
        assert self._client is not None
        payload = {
            "p_order_id": str(order_id),
            "p_trade_mode": trade_mode.value,
            "p_trading_date": trading_date.isoformat(),
            "p_symbol": symbol,
            "p_side": side,
            "p_risk_amount": str(risk_amount),
            "p_notional_amount": str(notional_amount),
        }
        resp = await self._client.post(
            "/rest/v1/rpc/gateway_check_and_reserve_risk",
            json=payload,
        )
        if resp.status_code >= 500:
            raise SupabaseError(
                "transient error: rpc=gateway_check_and_reserve_risk "
                f"status={resp.status_code} body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise SupabaseError(
                "rpc failed: rpc=gateway_check_and_reserve_risk "
                f"status={resp.status_code} body={resp.text[:200]}"
            )
        row = _single_rpc_row(resp.json(), rpc="gateway_check_and_reserve_risk")
        return RiskReservationDecision(
            passed=_parse_rpc_bool(row.get("passed"), field="passed"),
            reason=_parse_optional_str(row.get("reason"), field="reason"),
            reserved=_parse_rpc_bool(row.get("reserved"), field="reserved"),
            active_risk_before=_parse_decimal(
                row.get("active_risk_before"), field="active_risk_before"
            ),
            active_risk_after=_parse_decimal(
                row.get("active_risk_after"), field="active_risk_after"
            ),
            daily_pnl=_parse_decimal(row.get("daily_pnl"), field="daily_pnl"),
            daily_loss_limit=_parse_decimal(row.get("daily_loss_limit"), field="daily_loss_limit"),
            weekly_pnl=_parse_decimal(row.get("weekly_pnl"), field="weekly_pnl"),
            weekly_loss_limit=_parse_decimal(
                row.get("weekly_loss_limit"), field="weekly_loss_limit"
            ),
            monthly_pnl=_parse_decimal(row.get("monthly_pnl"), field="monthly_pnl"),
            monthly_loss_limit=_parse_decimal(
                row.get("monthly_loss_limit"), field="monthly_loss_limit"
            ),
        )

    async def release_risk_reservation(self, *, order_id: UUID, reason: str) -> bool:
        """Release an active reservation when publishing the order fails."""
        assert self._client is not None
        resp = await self._client.post(
            "/rest/v1/rpc/gateway_release_risk_reservation",
            json={"p_order_id": str(order_id), "p_reason": reason},
        )
        if resp.status_code >= 500:
            raise SupabaseError(
                "transient error: rpc=gateway_release_risk_reservation "
                f"status={resp.status_code} body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise SupabaseError(
                "rpc failed: rpc=gateway_release_risk_reservation "
                f"status={resp.status_code} body={resp.text[:200]}"
            )
        row = _single_rpc_row(resp.json(), rpc="gateway_release_risk_reservation")
        return _parse_rpc_bool(row.get("released"), field="released")

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
    async def read_latest_daily_liquidity(
        self, *, symbol: str
    ) -> DailyLiquiditySnapshot | None:
        """Return the latest daily close/volume/turnover for liquidity sizing."""
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/daily_ohlcv",
            params={
                "select": "close,volume,turnover",
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
        row = rows[0]
        try:
            close = Decimal(str(row["close"]))
            volume = int(row["volume"])
            turnover = Decimal(str(row["turnover"]))
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise SupabaseError(f"invalid daily_ohlcv liquidity row: {row}") from exc
        return DailyLiquiditySnapshot(close=close, volume=volume, turnover=turnover)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def read_capital_in_use(self, *, trade_mode: TradeMode) -> Decimal:
        """Return summed position value using current_price, falling back to entry_price."""
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/positions",
            params={
                "select": "quantity,current_price,entry_price",
                "trade_type": f"eq.{trade_mode.value}",
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
        if not isinstance(rows, list):
            raise SupabaseError(f"unexpected positions payload: {type(rows).__name__}")
        total = Decimal("0")
        for row in rows:
            if not isinstance(row, dict):
                raise SupabaseError(f"invalid position row: {row!r}")
            try:
                quantity = int(row["quantity"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SupabaseError(f"invalid position quantity: {row}") from exc
            if quantity <= 0:
                continue
            raw_price = row.get("current_price")
            if raw_price is None:
                raw_price = row.get("entry_price")
            if raw_price is None:
                continue
            try:
                price = Decimal(str(raw_price))
            except (InvalidOperation, ValueError) as exc:
                raise SupabaseError(f"invalid position price: {raw_price!r}") from exc
            if price <= 0:
                continue
            total += price * quantity
        return total

    async def read_live_capital_in_use(self) -> Decimal:
        """Return summed live position value using current_price, falling back to entry_price."""
        return await self.read_capital_in_use(trade_mode=TradeMode.LIVE)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def read_market_regime(self, *, valid_date: date) -> MarketRegimeState | None:
        """Return the market regime row for ``valid_date``.

        Missing rows are expected during rollout and mean "no regime override".
        A missing table is also treated as no-op so the log-only reader can be
        deployed before the migration is applied everywhere.
        """
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/market_regime",
            params={
                "select": (
                    "valid_date,regime,confidence,buy_enabled,position_size_multiplier,"
                    "source,rationale,metrics,created_at"
                ),
                "valid_date": f"eq.{valid_date.isoformat()}",
                "limit": "1",
            },
        )
        if resp.status_code == 404 or (resp.status_code == 400 and "market_regime" in resp.text):
            logger.warning("market_regime table unavailable; skipping regime guard")
            return None
        if resp.status_code >= 500:
            raise SupabaseError(
                f"transient error: table=market_regime status={resp.status_code} "
                f"body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise SupabaseError(
                f"read failed: table=market_regime status={resp.status_code} body={resp.text[:200]}"
            )
        rows = resp.json()
        if not isinstance(rows, list):
            raise SupabaseError(f"unexpected market_regime payload: {type(rows).__name__}")
        if not rows:
            return None
        row = rows[0]
        if not isinstance(row, dict):
            raise SupabaseError(f"invalid market_regime row: {row!r}")
        try:
            rationale = row.get("rationale", [])
            metrics = row.get("metrics", {})
            created_at_raw = row.get("created_at")
            created_at = (
                datetime.fromisoformat(str(created_at_raw).replace("Z", "+00:00"))
                if created_at_raw
                else None
            )
            if not isinstance(rationale, list):
                rationale = []
            if not isinstance(metrics, dict):
                metrics = {}
            return MarketRegimeState(
                valid_date=date.fromisoformat(str(row["valid_date"])),
                regime=str(row["regime"]),
                confidence=Decimal(str(row["confidence"])),
                buy_enabled=_parse_bool(row["buy_enabled"], field="buy_enabled"),
                position_size_multiplier=Decimal(str(row["position_size_multiplier"])),
                source=str(row.get("source") or "unknown"),
                rationale=rationale,
                metrics=metrics,
                created_at=created_at,
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise SupabaseError(f"invalid market_regime row: {row}") from exc

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def has_sell_since(self, *, symbol: str, trade_mode: TradeMode, since: datetime) -> bool:
        """Return True when the trade history has a SELL for ``symbol`` since ``since``."""
        assert self._client is not None
        table = "trades_live" if trade_mode is TradeMode.LIVE else "trades_paper"
        resp = await self._client.get(
            f"/rest/v1/{table}",
            params={
                "select": "trade_id",
                "symbol": f"eq.{symbol}",
                "side": "eq.SELL",
                "executed_at": f"gte.{since.isoformat()}",
                "limit": "1",
            },
        )
        if resp.status_code >= 500:
            raise SupabaseError(
                f"transient error: table={table} status={resp.status_code} body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise SupabaseError(
                f"read failed: table={table} status={resp.status_code} body={resp.text[:200]}"
            )
        rows = resp.json()
        if not isinstance(rows, list):
            raise SupabaseError(f"unexpected {table} payload: {type(rows).__name__}")
        return bool(rows)

    async def has_live_sell_since(self, *, symbol: str, since: datetime) -> bool:
        """Return True when ``trades_live`` has a SELL for ``symbol`` since ``since``."""
        return await self.has_sell_since(symbol=symbol, trade_mode=TradeMode.LIVE, since=since)

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
        logger.debug("supabase update: table=system_status is_trading_allowed=false")


def _parse_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    raise SupabaseError(f"invalid market_regime {field}: {value!r}")


def _parse_rpc_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise SupabaseError(f"invalid rpc bool {field}: {value!r}")


def _parse_optional_str(value: Any, *, field: str) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise SupabaseError(f"invalid rpc string {field}: {value!r}")


def _parse_decimal(value: Any, *, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SupabaseError(f"invalid rpc decimal {field}: {value!r}") from exc


def _single_rpc_row(payload: Any, *, rpc: str) -> dict[str, Any]:
    if isinstance(payload, list):
        if not payload:
            raise SupabaseError(f"{rpc} returned no rows")
        row = payload[0]
    elif isinstance(payload, dict):
        row = payload
    else:
        raise SupabaseError(f"unexpected {rpc} payload: {type(payload).__name__}")
    if not isinstance(row, dict):
        raise SupabaseError(f"unexpected {rpc} row: {type(row).__name__}")
    return row
