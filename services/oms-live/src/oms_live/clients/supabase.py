"""Supabase (PostgREST) client for OMS Live.

OMS Live が触る範囲:
* ``system_status`` の R (closeout の trading_style 判定) と W (live 約定確定後の
  ``daily_pnl`` / ``weekly_pnl`` / ``monthly_pnl`` 加算)
  - ``is_trading_allowed`` の自動 OFF (キルスイッチ発火) は **Gateway 側の責務**。
    OMS Live は pnl を更新するだけで、その値を見て Gateway が次回以降の注文を
    弾く。
* ``positions`` (trade_type='live') の CRUD
* ``trades_live`` の INSERT (1 約定 = 1 行、複数 ExecutionDetail は VWAP に集約済み)

oms-paper の SupabaseClient とほぼ同型。違いは:
* ``trade_type='live'``
* ``trades_live`` を書く
* ``add_realized_pnl()`` を持つ (oms-paper には無い)

PostgREST 直叩き。Supabase SDK は使わない。fail-closed で不正レスポンス時は
例外を投げ、Pub/Sub redelivery に委ねる。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Self
from uuid import UUID

import httpx
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from trade_contracts.risk import KillSwitchState

from ..models import LiveFillRecord, LivePosition

logger = logging.getLogger(__name__)


class SupabaseError(RuntimeError):
    """Supabase (PostgREST) error wrapper."""


@dataclass(slots=True)
class SupabaseClient:
    """OMS Live streaming loop で使う Supabase クライアント。"""

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
    async def add_realized_pnl(self, amount: Decimal) -> None:
        """``daily_pnl`` / ``weekly_pnl`` / ``monthly_pnl`` に同額を加算する。

        PostgREST 単独では atomic な ``column = column + delta`` が書けないため、
        read → compute → PATCH の二段階で行う。OMS Live は単一プロセスなので
        race にはならないが、複数インスタンス化する場合は Postgres 関数 (RPC)
        への切替が必要になる。

        ``amount`` は実現損益。BUY 時は呼ばないこと (entry なので確定なし)。
        """
        if amount == Decimal("0"):
            return
        assert self._client is not None
        state = await self.read_system_status()
        new_daily = state.daily_pnl + amount
        new_weekly = state.weekly_pnl + amount
        new_monthly = state.monthly_pnl + amount
        resp = await self._client.patch(
            "/rest/v1/system_status",
            params={"id": "eq.1"},
            headers={"Prefer": "return=minimal"},
            json={
                "daily_pnl": str(new_daily),
                "weekly_pnl": str(new_weekly),
                "monthly_pnl": str(new_monthly),
            },
        )
        self._raise_for_status(resp, table="system_status", op="update")
        logger.debug(
            "supabase update: system_status pnl_delta=%s daily=%s weekly=%s monthly=%s",
            amount,
            new_daily,
            new_weekly,
            new_monthly,
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def read_live_position(self, *, symbol: str) -> LivePosition | None:
        """``(symbol, trade_type='live')`` の行を読み、LivePosition で返す。

        該当行が無ければ ``None``。``current_price`` / ``unrealized_pnl`` は
        Feature Engine が更新する列なので無視する。
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
                "trade_type": "eq.live",
                "limit": "1",
            },
        )
        self._raise_for_status(resp, table="positions", op="read")
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            return None
        return _parse_live_position(rows[0])

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def list_live_positions(self) -> list[LivePosition]:
        """``trade_type='live'`` の全 positions を返す (closeout 用)。"""
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/positions",
            params={
                "select": (
                    "symbol,quantity,entry_price,holding_type,"
                    "target_price,stop_loss_price,max_hold_days,"
                    "trailing_stop_pct,opened_at,side"
                ),
                "trade_type": "eq.live",
            },
        )
        self._raise_for_status(resp, table="positions", op="read")
        rows = resp.json()
        if not isinstance(rows, list):
            raise SupabaseError(f"unexpected positions list payload: {type(rows).__name__}")
        return [_parse_live_position(r) for r in rows]

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def insert_live_position(self, position: LivePosition) -> None:
        """新規 live position を INSERT する。

        ``current_price`` は ``entry_price`` で初期化、``unrealized_pnl`` は 0。
        以降の更新は Feature Engine の責務 (OMS Live は触らない)。
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
            "supabase insert: positions(live) symbol=%s qty=%d", position.symbol, position.quantity
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def update_live_position_quantity(
        self, *, symbol: str, quantity: int, entry_price: str
    ) -> None:
        """既存 live position の ``quantity`` / ``entry_price`` を PATCH。

        ``current_price`` / ``unrealized_pnl`` は Feature Engine 管理なので
        ここでは触らない。
        """
        assert self._client is not None
        resp = await self._client.patch(
            "/rest/v1/positions",
            params={"symbol": f"eq.{symbol}", "trade_type": "eq.live"},
            headers={"Prefer": "return=minimal"},
            json={"quantity": quantity, "entry_price": entry_price},
        )
        self._raise_for_status(resp, table="positions", op="update")
        logger.debug(
            "supabase update: positions(live) symbol=%s qty=%d entry=%s",
            symbol,
            quantity,
            entry_price,
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def update_live_position_reconciled(
        self,
        *,
        symbol: str,
        quantity: int,
        entry_price: str,
        current_price: str | None = None,
        unrealized_pnl: str | None = None,
    ) -> None:
        """reconcile 用に live position を kabu 実残へ明示補正する。"""
        assert self._client is not None
        payload: dict[str, Any] = {"quantity": quantity, "entry_price": entry_price}
        if current_price is not None:
            payload["current_price"] = current_price
        if unrealized_pnl is not None:
            payload["unrealized_pnl"] = unrealized_pnl
        resp = await self._client.patch(
            "/rest/v1/positions",
            params={"symbol": f"eq.{symbol}", "trade_type": "eq.live"},
            headers={"Prefer": "return=minimal"},
            json=payload,
        )
        self._raise_for_status(resp, table="positions", op="update")
        logger.warning(
            "supabase reconcile update: positions(live) symbol=%s qty=%d entry=%s "
            "current=%s pnl=%s",
            symbol,
            quantity,
            entry_price,
            current_price,
            unrealized_pnl,
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def delete_live_position(self, *, symbol: str) -> None:
        """``(symbol, trade_type='live')`` の行を DELETE。冪等。"""
        assert self._client is not None
        resp = await self._client.delete(
            "/rest/v1/positions",
            params={"symbol": f"eq.{symbol}", "trade_type": "eq.live"},
            headers={"Prefer": "return=minimal"},
        )
        self._raise_for_status(resp, table="positions", op="delete")
        logger.debug("supabase delete: positions(live) symbol=%s", symbol)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def insert_trade_live(self, record: LiveFillRecord) -> None:
        """実約定を ``trades_live`` に INSERT する (1 約定 = 1 行)。

        ``record.order_id`` が付いていれば DB の partial unique index で二重
        INSERT は弾かれる (23505 → 409)。アプリ層では ``live_trade_exists_for_order_id``
        による事前 check と組み合わせて二段で守る。
        """
        assert self._client is not None
        row = {
            "trade_id": str(record.trade_id),
            "order_id": str(record.order_id) if record.order_id is not None else None,
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
            "/rest/v1/trades_live",
            headers={"Prefer": "return=minimal"},
            json=[row],
        )
        self._raise_for_status(resp, table="trades_live", op="insert")
        logger.debug(
            "supabase insert: trades_live trade_id=%s order_id=%s symbol=%s side=%s qty=%d",
            record.trade_id,
            record.order_id,
            record.symbol,
            record.side.value,
            record.quantity,
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, SupabaseError)),
    )
    async def live_trade_exists_for_order_id(self, order_id: UUID) -> bool:
        """``trades_live`` に ``order_id`` 一致行が存在するかを返す。

        Runner が ``insert_trade_live`` の前に呼ぶ。``True`` なら冪等性のため
        新規発注をスキップし ack する。``select=order_id&limit=1`` で軽量化。
        """
        assert self._client is not None
        resp = await self._client.get(
            "/rest/v1/trades_live",
            params={
                "select": "order_id",
                "order_id": f"eq.{order_id}",
                "limit": "1",
            },
        )
        self._raise_for_status(resp, table="trades_live", op="read")
        rows = resp.json()
        if not isinstance(rows, list):
            raise SupabaseError(f"unexpected trades_live payload: {type(rows).__name__}")
        return len(rows) > 0

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


def _parse_live_position(row: dict[str, Any]) -> LivePosition:
    """PostgREST 行 → LivePosition。defensive に side='LONG' を確認する。"""
    side = str(row.get("side", "LONG"))
    if side != "LONG":
        raise SupabaseError(
            f"unexpected non-LONG live position: symbol={row.get('symbol')} side={side}"
        )
    try:
        return LivePosition.model_validate(row)
    except ValidationError as exc:
        raise SupabaseError(f"invalid live position row: {exc}") from exc


def _position_to_insert_row(position: LivePosition) -> dict[str, Any]:
    entry = str(position.entry_price)
    row: dict[str, Any] = {
        "symbol": position.symbol,
        "trade_type": "live",
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
