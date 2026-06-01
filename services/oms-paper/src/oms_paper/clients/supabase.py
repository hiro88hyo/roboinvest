"""Supabase (PostgREST) client for OMS Paper.

OMS Paper が触る範囲:
* ``system_status`` の R (14:50 closeout の trading_style 判定のみ。書き込みなし)
* ``positions`` (trade_type='paper') の CRUD
  - 1 銘柄の取得 (apply_fill 用に既存 PaperPosition を読む)
  - 全件取得 (closeout 用)
  - INSERT (新規ポジション)
  - PATCH (quantity / entry_price 更新。current_price と unrealized_pnl は
    Feature Engine が更新するため OMS Paper は触らない)
  - DELETE (全決済)
* ``trades_paper`` の INSERT (約定 1 件 = 1 行)

PostgREST 直叩き。Supabase SDK は使わない。fail-closed で不正レスポンス時は
例外を投げ、Pub/Sub redelivery に委ねる。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Self
from uuid import UUID

import httpx
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from trade_contracts.risk import KillSwitchState

from ..models import PaperFillRecord, PaperPosition

logger = logging.getLogger(__name__)


class SupabaseError(RuntimeError):
    """Supabase (PostgREST) error wrapper."""


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
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
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
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def read_paper_position(self, *, symbol: str) -> PaperPosition | None:
        """``(symbol, trade_type='paper')`` の行を読み、PaperPosition で返す。

        該当行が無ければ ``None``。``current_price`` / ``unrealized_pnl`` 列は
        ``PaperPosition`` に含まれないので無視する。
        """
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/positions",
            params={
                "select": (
                    "symbol,quantity,entry_price,holding_type,"
                    "target_price,stop_loss_price,max_hold_days,"
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
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def list_paper_positions(self) -> list[PaperPosition]:
        """``trade_type='paper'`` の全 positions を返す (closeout 用)。"""
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/positions",
            params={
                "select": (
                    "symbol,quantity,entry_price,holding_type,"
                    "target_price,stop_loss_price,max_hold_days,"
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
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def insert_paper_position(self, position: PaperPosition) -> None:
        """新規 paper position を INSERT する。

        ``current_price`` は ``entry_price`` で初期化、``unrealized_pnl`` は 0。
        以降の ``current_price`` / ``unrealized_pnl`` 更新は Feature Engine の
        責務 (OMS Paper は触らない)。
        """
        assert self._client is not None
        row = _position_to_insert_row(position)
        resp = await self._client.post(
            "/rest/v1/positions",
            headers={"Prefer": "return=minimal"},
            json=[row],
        )
        self._raise_for_status(resp, table="positions", op="insert")
        logger.debug(
            "supabase insert: positions symbol=%s qty=%d", position.symbol, position.quantity
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def update_paper_position_quantity(
        self, *, symbol: str, quantity: int, entry_price: str
    ) -> None:
        """既存 paper position の ``quantity`` / ``entry_price`` を PATCH。

        ``current_price`` / ``unrealized_pnl`` は Feature Engine 管理なので
        ここでは触らない。``entry_price`` は文字列で受け取り、Decimal の
        丸めを呼び出し側に委ねる。
        """
        assert self._client is not None
        resp = await self._client.patch(
            "/rest/v1/positions",
            params={"symbol": f"eq.{symbol}", "trade_type": "eq.paper"},
            headers={"Prefer": "return=minimal"},
            json={"quantity": quantity, "entry_price": entry_price},
        )
        self._raise_for_status(resp, table="positions", op="update")
        logger.debug(
            "supabase update: positions symbol=%s qty=%d entry=%s", symbol, quantity, entry_price
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def update_paper_position_stop_loss(self, *, symbol: str, stop_loss_price: str) -> None:
        """既存 paper position の ``stop_loss_price`` のみを PATCH。

        swing トレーリングストップ用。``quantity`` / ``entry_price`` は触らない。
        ``stop_loss_price`` は文字列で受け取り、Decimal の丸めを呼び出し側
        (``swing_monitor.evaluate_swing_exit``) に委ねる。
        """
        assert self._client is not None
        resp = await self._client.patch(
            "/rest/v1/positions",
            params={"symbol": f"eq.{symbol}", "trade_type": "eq.paper"},
            headers={"Prefer": "return=minimal"},
            json={"stop_loss_price": stop_loss_price},
        )
        self._raise_for_status(resp, table="positions", op="update_stop_loss")
        logger.debug("supabase update: positions symbol=%s stop_loss=%s", symbol, stop_loss_price)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def delete_paper_position(self, *, symbol: str) -> None:
        """``(symbol, trade_type='paper')`` の行を DELETE。冪等。"""
        assert self._client is not None
        resp = await self._client.delete(
            "/rest/v1/positions",
            params={"symbol": f"eq.{symbol}", "trade_type": "eq.paper"},
            headers={"Prefer": "return=minimal"},
        )
        self._raise_for_status(resp, table="positions", op="delete")
        logger.debug("supabase delete: positions symbol=%s", symbol)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def paper_trade_exists_for_signal(self, signal_id: UUID) -> bool:
        """unified_signal_id が既に trades_paper に存在するか確認する (冪等性チェック)。"""
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/trades_paper",
            params={
                "select": "trade_id",
                "unified_signal_id": f"eq.{signal_id}",
                "limit": "1",
            },
        )
        self._raise_for_status(resp, table="trades_paper", op="exists_check")
        rows = resp.json()
        return isinstance(rows, list) and len(rows) > 0

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def insert_trade_paper(self, record: PaperFillRecord) -> None:
        """擬似約定を ``trades_paper`` に INSERT する (1 約定 = 1 行)。"""
        assert self._client is not None
        row = {
            "trade_id": str(record.trade_id),
            "symbol": record.symbol,
            "side": record.side.value,
            "quantity": record.quantity,
            "price": str(record.price),
            "signal_source": record.signal_source.value,
            "unified_signal_id": (
                str(record.unified_signal_id) if record.unified_signal_id is not None else None
            ),
            "executed_at": record.executed_at.isoformat(),
        }
        resp = await self._client.post(
            "/rest/v1/trades_paper",
            headers={"Prefer": "return=minimal"},
            json=[row],
        )
        self._raise_for_status(resp, table="trades_paper", op="insert")
        logger.debug(
            "supabase insert: trades_paper trade_id=%s symbol=%s side=%s qty=%d",
            record.trade_id,
            record.symbol,
            record.side.value,
            record.quantity,
        )

    def _raise_for_status(self, resp: httpx.Response, *, table: str, op: str) -> None:
        if resp.status_code >= 500:
            raise SupabaseError(
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


def _position_to_insert_row(position: PaperPosition) -> dict[str, Any]:
    entry = str(position.entry_price)
    row: dict[str, Any] = {
        "symbol": position.symbol,
        "trade_type": "paper",
        "side": "LONG",
        "quantity": position.quantity,
        "entry_price": entry,
        "current_price": entry,
        "unrealized_pnl": "0",
        "holding_type": position.holding_type.value,
        "opened_at": position.opened_at.isoformat(),
    }
    if position.target_price is not None:
        row["target_price"] = str(position.target_price)
    if position.stop_loss_price is not None:
        row["stop_loss_price"] = str(position.stop_loss_price)
    if position.max_hold_days is not None:
        row["max_hold_days"] = position.max_hold_days
    if position.trailing_stop_pct is not None:
        row["trailing_stop_pct"] = str(position.trailing_stop_pct)
    return row
