"""Supabase (PostgREST) client for OMS Paper.

OMS Paper が触る範囲:
* ``system_status`` の R (14:50 closeout の trading_style 判定のみ。書き込みなし)
* ``positions`` (trade_type='paper') の R (closeout / monitor 用)
* ``oms_paper_update_stop_loss`` RPC (generation-checked trailing stop)
* ``oms_paper_apply_fill`` RPC (全 fill の約定 + position 遷移を原子的に永続化)

fill 用の direct INSERT / UPDATE / DELETE API は意図的に公開しない。通常注文、
closeout、swing/day stop の全経路を RPC に限定し、2 段書き込みへの回帰を防ぐ。

PostgREST 直叩き。Supabase SDK は使わない。fail-closed で不正レスポンス時は
例外を投げ、Pub/Sub redelivery に委ねる。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Self

import httpx
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from trade_contracts.enums import Side, TradingStyle
from trade_contracts.risk import KillSwitchState

from ..models import (
    PaperFillApplyResult,
    PaperFillRecord,
    PaperPosition,
    PaperStopUpdateResult,
)

logger = logging.getLogger(__name__)


class SupabaseError(RuntimeError):
    """Supabase (PostgREST) error wrapper."""


class SupabaseTransientError(SupabaseError):
    """Retryable transport/server-side Supabase failure."""


@dataclass(slots=True)
class SupabaseClient:
    """OMS Paper streaming loop で使う Supabase クライアント。"""

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
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseTransientError)),
    )
    async def read_system_status(self) -> KillSwitchState:
        """``id=1`` 行を読み、KillSwitchState にパースする。"""
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/system_status",
            params={"select": "*", "id": "eq.1", "limit": "1"},
        )
        self._raise_for_status(resp, table="system_status", op="read")
        payload = resp.json()
        if not isinstance(payload, list) or not payload:
            raise SupabaseError("system_status row (id=1) not found")
        try:
            return KillSwitchState.model_validate(payload[0])
        except ValidationError as exc:
            raise SupabaseError(f"invalid system_status row: {exc}") from exc

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseTransientError)),
    )
    async def read_paper_position(self, *, symbol: str) -> PaperPosition | None:
        """``(symbol, trade_type='paper')`` の行を読み、PaperPosition で返す。

        monitor が cached position の実在を約定直前に確認するために使う。
        該当行が無ければ ``None``。``current_price`` / ``unrealized_pnl`` 列は
        ``PaperPosition`` に含まれないので無視する。
        """
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/positions",
            params={
                "select": (
                    "symbol,quantity,entry_price,holding_type,"
                    "target_price,stop_loss_price,max_hold_days,scheduled_exit_date,"
                    "trailing_stop_pct,opened_at,side"
                ),
                "symbol": f"eq.{symbol}",
                "trade_type": "eq.paper",
                "limit": "1",
            },
        )
        self._raise_for_status(resp, table="positions", op="read")
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            return None
        return _parse_paper_position(rows[0])

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseTransientError)),
    )
    async def list_paper_positions(self) -> list[PaperPosition]:
        """``trade_type='paper'`` の全 positions を返す (closeout 用)。"""
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/positions",
            params={
                "select": (
                    "symbol,quantity,entry_price,holding_type,"
                    "target_price,stop_loss_price,max_hold_days,scheduled_exit_date,"
                    "trailing_stop_pct,opened_at,side"
                ),
                "trade_type": "eq.paper",
            },
        )
        self._raise_for_status(resp, table="positions", op="read")
        rows = resp.json()
        if not isinstance(rows, list):
            raise SupabaseError(f"unexpected positions list payload: {type(rows).__name__}")
        return [_parse_paper_position(r) for r in rows]

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseTransientError)),
    )
    async def update_paper_position_stop_loss(
        self,
        *,
        symbol: str,
        expected_opened_at: datetime,
        stop_loss_price: str,
    ) -> PaperStopUpdateResult:
        """``opened_at`` を照合し、trailing stop だけを RPC 更新する。

        fill と同じ symbol advisory lock に参加するため、古い position A の判断を
        同じ symbol の新しい position B へ誤適用しない。
        """
        assert self._client is not None
        resp = await self._client.post(
            "/rest/v1/rpc/oms_paper_update_stop_loss",
            json={
                "p_symbol": symbol,
                "p_expected_position_opened_at": expected_opened_at.isoformat(),
                "p_stop_loss_price": stop_loss_price,
            },
        )
        self._raise_for_status(resp, table="oms_paper_update_stop_loss", op="rpc")
        result = _parse_stop_update_result(resp)
        logger.debug(
            "supabase rpc: stop update symbol=%s outcome=%s stop_loss=%s",
            symbol,
            result.outcome.value,
            stop_loss_price,
        )
        return result

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseTransientError)),
    )
    async def apply_paper_fill(
        self,
        *,
        record: PaperFillRecord,
        new_holding_type: TradingStyle | None,
        expected_position_opened_at: datetime | None = None,
        new_target_price: Decimal | None = None,
        new_stop_loss_price: Decimal | None = None,
        new_max_hold_days: int | None = None,
        new_scheduled_exit_date: date | None = None,
        new_trailing_stop_pct: Decimal | None = None,
    ) -> PaperFillApplyResult:
        """約定と position 遷移を ``oms_paper_apply_fill`` で原子的に確定する。

        BUY は RPC 実行時点で position が存在しない可能性を常に考慮し、
        ``new_holding_type`` を必須とする。返却された position が Supabase 上の
        authoritative state であり、呼び出し側の事前 read より優先する。
        """

        assert self._client is not None
        if record.side is Side.BUY and new_holding_type is None:
            raise SupabaseError("new_holding_type is required for BUY apply_paper_fill")

        params = {
            "p_order_id": str(record.order_id),
            "p_trade_id": str(record.trade_id),
            "p_symbol": record.symbol,
            "p_side": record.side.value,
            "p_filled_quantity": record.quantity,
            "p_fill_price": str(record.price),
            "p_signal_source": record.signal_source.value,
            "p_unified_signal_id": (
                str(record.unified_signal_id) if record.unified_signal_id is not None else None
            ),
            "p_executed_at": record.executed_at.isoformat(),
            "p_expected_position_opened_at": (
                expected_position_opened_at.isoformat()
                if expected_position_opened_at is not None
                else None
            ),
            "p_new_holding_type": (
                new_holding_type.value if new_holding_type is not None else None
            ),
            "p_new_target_price": (str(new_target_price) if new_target_price is not None else None),
            "p_new_stop_loss_price": (
                str(new_stop_loss_price) if new_stop_loss_price is not None else None
            ),
            "p_new_max_hold_days": new_max_hold_days,
            "p_new_scheduled_exit_date": (
                new_scheduled_exit_date.isoformat() if new_scheduled_exit_date is not None else None
            ),
            "p_new_trailing_stop_pct": (
                str(new_trailing_stop_pct) if new_trailing_stop_pct is not None else None
            ),
        }
        resp = await self._client.post(
            "/rest/v1/rpc/oms_paper_apply_fill",
            json=params,
        )
        self._raise_for_status(resp, table="oms_paper_apply_fill", op="rpc")
        result = _parse_apply_fill_result(resp)
        logger.debug(
            "supabase rpc: oms_paper_apply_fill order_id=%s outcome=%s action=%s",
            record.order_id,
            result.outcome.value,
            result.position_action.value,
        )
        return result

    def _raise_for_status(self, resp: httpx.Response, *, table: str, op: str) -> None:
        if resp.status_code >= 500:
            raise SupabaseTransientError(
                f"transient error: table={table} op={op} status={resp.status_code} "
                f"body={resp.text[:200]}"
            )
        if resp.status_code >= 300:
            raise SupabaseError(
                f"{op} failed: table={table} status={resp.status_code} body={resp.text[:200]}"
            )


def _parse_paper_position(row: dict[str, Any]) -> PaperPosition:
    """PostgREST 行 → PaperPosition。defensive に side='LONG' を確認する。"""
    side = str(row.get("side", "LONG"))
    if side != "LONG":
        raise SupabaseError(
            f"unexpected non-LONG paper position: symbol={row.get('symbol')} side={side}"
        )
    try:
        return PaperPosition.model_validate(row)
    except ValidationError as exc:
        raise SupabaseError(f"invalid paper position row: {exc}") from exc


def _parse_apply_fill_result(resp: httpx.Response) -> PaperFillApplyResult:
    """Strictly parse the RPC's single result row and nested authoritative position."""

    try:
        payload: object = resp.json()
    except ValueError as exc:
        raise SupabaseError("invalid oms_paper_apply_fill response: malformed JSON") from exc
    if not isinstance(payload, list) or len(payload) != 1:
        raise SupabaseError("invalid oms_paper_apply_fill response: expected exactly one row")
    raw_row = payload[0]
    if not isinstance(raw_row, dict) or not all(isinstance(key, str) for key in raw_row):
        raise SupabaseError("invalid oms_paper_apply_fill response: row must be an object")

    row: dict[str, Any] = dict(raw_row)
    expected_keys = {
        "outcome",
        "reason",
        "committed_trade_id",
        "position_action",
        "resulting_position",
    }
    if set(row) != expected_keys:
        raise SupabaseError("invalid oms_paper_apply_fill response: unexpected result columns")

    raw_position = row["resulting_position"]
    if raw_position is not None:
        if not isinstance(raw_position, dict) or not all(
            isinstance(key, str) for key in raw_position
        ):
            raise SupabaseError(
                "invalid oms_paper_apply_fill response: resulting_position must be an object"
            )
        row["resulting_position"] = _parse_paper_position(dict(raw_position))

    try:
        return PaperFillApplyResult.model_validate(row)
    except ValidationError as exc:
        raise SupabaseError(f"invalid oms_paper_apply_fill response: {exc}") from exc


def _parse_stop_update_result(resp: httpx.Response) -> PaperStopUpdateResult:
    """Strictly parse the generation-checked trailing-stop RPC response."""

    try:
        payload: object = resp.json()
    except ValueError as exc:
        raise SupabaseError("invalid oms_paper_update_stop_loss response: malformed JSON") from exc
    if not isinstance(payload, list) or len(payload) != 1:
        raise SupabaseError("invalid oms_paper_update_stop_loss response: expected one row")
    raw_row = payload[0]
    if not isinstance(raw_row, dict) or set(raw_row) != {
        "outcome",
        "reason",
        "resulting_position",
    }:
        raise SupabaseError("invalid oms_paper_update_stop_loss response: unexpected columns")

    row: dict[str, Any] = dict(raw_row)
    raw_position = row["resulting_position"]
    if raw_position is not None:
        if not isinstance(raw_position, dict):
            raise SupabaseError(
                "invalid oms_paper_update_stop_loss response: position must be an object"
            )
        row["resulting_position"] = _parse_paper_position(dict(raw_position))
    try:
        return PaperStopUpdateResult.model_validate(row)
    except ValidationError as exc:
        raise SupabaseError(f"invalid oms_paper_update_stop_loss response: {exc}") from exc
