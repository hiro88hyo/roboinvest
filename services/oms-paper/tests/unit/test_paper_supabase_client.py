from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from oms_paper.clients.supabase import (
    SupabaseClient,
    SupabaseError,
    _parse_apply_fill_result,
)
from oms_paper.models import (
    FillResult,
    PaperFillOutcome,
    PaperFillRecord,
    PaperPositionAction,
    PaperStopUpdateOutcome,
)
from oms_paper.position_updater import build_fill_record
from trade_contracts.enums import Side, TradingStyle
from trade_contracts.risk import KillSwitchState

from oms_paper._testing import make_order_request  # isort: skip

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


def _build_client(handler: Handler) -> SupabaseClient:
    return SupabaseClient(
        url="https://example.supabase.co",
        secret_key="k",
        transport=httpx.MockTransport(handler),
    )


def _system_status_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": 1,
        "is_trading_allowed": True,
        "trade_mode": "paper",
        "trading_style": "day",
        "daily_pnl": "0",
        "weekly_pnl": "0",
        "monthly_pnl": "0",
        "daily_loss_limit": "10000",
        "weekly_loss_limit": "30000",
        "monthly_loss_limit": "100000",
        "updated_at": "2026-04-25T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _position_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": "7203",
        "side": "LONG",
        "quantity": 100,
        "entry_price": "1000",
        "holding_type": "day",
        "target_price": None,
        "stop_loss_price": None,
        "max_hold_days": None,
        "trailing_stop_pct": None,
        "opened_at": "2026-04-25T09:00:00+00:00",
    }
    row.update(overrides)
    return row


# --- system_status -----------------------------------------------------------


async def test_read_system_status_parses_row() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[_system_status_row()])

    async with _build_client(_handler) as client:
        state = await client.read_system_status()

    assert isinstance(state, KillSwitchState)
    assert state.trading_style is TradingStyle.DAY
    assert captured[0].url.path == "/rest/v1/system_status"
    assert captured[0].url.params.get("id") == "eq.1"


async def test_read_system_status_raises_when_missing() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="not found"):
            await client.read_system_status()


async def test_read_system_status_raises_on_invalid_row() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1, "is_trading_allowed": True}])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="invalid"):
            await client.read_system_status()


# --- read_paper_position -----------------------------------------------------


async def test_read_paper_position_returns_none_when_no_row() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        pos = await client.read_paper_position(symbol="7203")

    assert pos is None
    assert captured[0].url.params.get("symbol") == "eq.7203"
    assert captured[0].url.params.get("trade_type") == "eq.paper"


async def test_read_paper_position_parses_full_row() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_position_row(quantity=300, entry_price="1234.5")])

    async with _build_client(_handler) as client:
        pos = await client.read_paper_position(symbol="7203")

    assert pos is not None
    assert pos.quantity == 300
    assert pos.entry_price == Decimal("1234.5")
    assert pos.holding_type is TradingStyle.DAY


async def test_read_paper_position_rejects_non_long_side() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_position_row(side="SHORT")])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="non-LONG"):
            await client.read_paper_position(symbol="7203")


async def test_read_paper_position_raises_on_invalid_row() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_position_row(quantity=-10)])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="invalid paper position"):
            await client.read_paper_position(symbol="7203")


# --- list_paper_positions ----------------------------------------------------


async def test_list_paper_positions_returns_all() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                _position_row(symbol="7203", quantity=100),
                _position_row(symbol="9984", quantity=200, entry_price="8000"),
            ],
        )

    async with _build_client(_handler) as client:
        rows = await client.list_paper_positions()

    assert [p.symbol for p in rows] == ["7203", "9984"]
    assert captured[0].url.params.get("trade_type") == "eq.paper"
    assert "symbol" not in captured[0].url.params  # no symbol filter


async def test_list_paper_positions_returns_empty() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        rows = await client.list_paper_positions()
    assert rows == []


async def test_list_paper_positions_raises_on_non_list() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="unexpected positions list"):
            await client.list_paper_positions()


# --- update_paper_position_stop_loss -----------------------------------------


async def test_update_paper_position_stop_loss_posts_generation_checked_rpc() -> None:
    captured: list[httpx.Request] = []
    opened_at = datetime(2026, 4, 25, 9, 0, tzinfo=UTC)

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "outcome": "applied",
                    "reason": None,
                    "resulting_position": _position_row(
                        opened_at=opened_at.isoformat(),
                        stop_loss_price="1078",
                    ),
                }
            ],
        )

    async with _build_client(_handler) as client:
        result = await client.update_paper_position_stop_loss(
            symbol="7203",
            expected_opened_at=opened_at,
            stop_loss_price="1078",
        )

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "POST"
    assert req.url.path == "/rest/v1/rpc/oms_paper_update_stop_loss"
    assert json.loads(req.content.decode()) == {
        "p_symbol": "7203",
        "p_expected_position_opened_at": opened_at.isoformat(),
        "p_stop_loss_price": "1078",
    }
    assert result.outcome is PaperStopUpdateOutcome.APPLIED
    assert result.resulting_position is not None
    assert result.resulting_position.stop_loss_price == Decimal("1078")


async def test_update_paper_position_stop_loss_raises_on_4xx() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="row not found")

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="rpc failed"):
            await client.update_paper_position_stop_loss(
                symbol="missing",
                expected_opened_at=datetime(2026, 4, 25, 9, 0, tzinfo=UTC),
                stop_loss_price="1000",
            )


async def test_update_paper_position_stop_loss_rejects_zero_updated_rows() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="expected one row"):
            await client.update_paper_position_stop_loss(
                symbol="missing",
                expected_opened_at=datetime(2026, 4, 25, 9, 0, tzinfo=UTC),
                stop_loss_price="1000",
            )


async def test_update_paper_position_stop_loss_retries_on_5xx() -> None:
    calls = 0

    async def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, text="boom")
        return httpx.Response(
            200,
            json=[
                {
                    "outcome": "applied",
                    "reason": None,
                    "resulting_position": _position_row(stop_loss_price="1078"),
                }
            ],
        )

    async with _build_client(_handler) as client:
        await client.update_paper_position_stop_loss(
            symbol="7203",
            expected_opened_at=datetime(2026, 4, 25, 9, 0, tzinfo=UTC),
            stop_loss_price="1078",
        )

    assert calls == 3  # 5xx 2 回 → 3 回目で成功


def _build_fill_record() -> PaperFillRecord:
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100)
    fill = FillResult(filled_quantity=100, fill_price=Decimal("1000"), reason="filled")
    rec = build_fill_record(
        order=order,
        fill=fill,
        executed_at=datetime(2026, 4, 25, 9, 0, tzinfo=UTC),
    )
    assert rec is not None
    return rec


# --- apply_paper_fill --------------------------------------------------------


async def test_apply_paper_fill_posts_exact_rpc_params_and_parses_position() -> None:
    captured: list[httpx.Request] = []
    rec = _build_fill_record()

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "outcome": "applied",
                    "reason": None,
                    "committed_trade_id": str(rec.trade_id),
                    "position_action": "inserted",
                    "resulting_position": _position_row(
                        holding_type="swing",
                        entry_price="1000",
                        target_price="1200",
                        stop_loss_price="902.70",
                        max_hold_days=5,
                        scheduled_exit_date="2026-05-01",
                        trailing_stop_pct="0.03",
                    ),
                }
            ],
        )

    async with _build_client(_handler) as client:
        result = await client.apply_paper_fill(
            record=rec,
            new_holding_type=TradingStyle.SWING,
            new_target_price=Decimal("1200"),
            new_stop_loss_price=Decimal("902.70"),
            new_max_hold_days=5,
            new_scheduled_exit_date=date(2026, 5, 1),
            new_trailing_stop_pct=Decimal("0.03"),
        )

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "POST"
    assert req.url.path == "/rest/v1/rpc/oms_paper_apply_fill"
    assert json.loads(req.content.decode()) == {
        "p_order_id": str(rec.order_id),
        "p_trade_id": str(rec.trade_id),
        "p_symbol": "7203",
        "p_side": "BUY",
        "p_filled_quantity": 100,
        "p_fill_price": "1000",
        "p_signal_source": rec.signal_source.value,
        "p_unified_signal_id": str(rec.unified_signal_id),
        "p_executed_at": rec.executed_at.isoformat(),
        "p_expected_position_opened_at": None,
        "p_new_holding_type": "swing",
        "p_new_target_price": "1200",
        "p_new_stop_loss_price": "902.70",
        "p_new_max_hold_days": 5,
        "p_new_scheduled_exit_date": "2026-05-01",
        "p_new_trailing_stop_pct": "0.03",
    }
    assert result.outcome is PaperFillOutcome.APPLIED
    assert result.position_action is PaperPositionAction.INSERTED
    assert result.committed_trade_id == rec.trade_id
    assert result.resulting_position is not None
    assert result.resulting_position.symbol == "7203"
    assert result.resulting_position.holding_type is TradingStyle.SWING
    assert result.resulting_position.stop_loss_price == Decimal("902.70")
    assert result.resulting_position.scheduled_exit_date == date(2026, 5, 1)


async def test_apply_paper_fill_posts_null_new_position_params_for_sell() -> None:
    captured: list[httpx.Request] = []
    order = make_order_request(symbol="7203", side=Side.SELL, quantity=100)
    fill = FillResult(filled_quantity=100, fill_price=Decimal("1100"), reason="filled")
    rec = build_fill_record(order=order, fill=fill, executed_at=order.created_at)
    assert rec is not None

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "outcome": "applied",
                    "reason": None,
                    "committed_trade_id": str(rec.trade_id),
                    "position_action": "deleted",
                    "resulting_position": None,
                }
            ],
        )

    expected_opened_at = datetime(2026, 4, 24, 9, 0, tzinfo=UTC)
    async with _build_client(_handler) as client:
        result = await client.apply_paper_fill(
            record=rec,
            new_holding_type=None,
            expected_position_opened_at=expected_opened_at,
        )

    body = json.loads(captured[0].content.decode())
    assert body["p_unified_signal_id"] == str(rec.unified_signal_id)
    assert body["p_expected_position_opened_at"] == expected_opened_at.isoformat()
    assert body["p_new_holding_type"] is None
    assert body["p_new_target_price"] is None
    assert body["p_new_stop_loss_price"] is None
    assert body["p_new_max_hold_days"] is None
    assert body["p_new_scheduled_exit_date"] is None
    assert body["p_new_trailing_stop_pct"] is None
    assert result.position_action is PaperPositionAction.DELETED
    assert result.resulting_position is None


def test_parse_apply_fill_result_requires_exactly_one_row() -> None:
    payloads: tuple[object, ...] = ([], [{}, {}], {})
    for payload in payloads:
        response = httpx.Response(200, json=payload)
        with pytest.raises(SupabaseError, match="expected exactly one row"):
            _parse_apply_fill_result(response)


def test_parse_apply_fill_result_rejects_invalid_authoritative_position() -> None:
    response = httpx.Response(
        200,
        json=[
            {
                "outcome": "applied",
                "reason": None,
                "committed_trade_id": str(uuid4()),
                "position_action": "inserted",
                "resulting_position": _position_row(side="SHORT"),
            }
        ],
    )

    with pytest.raises(SupabaseError, match="unexpected non-LONG paper position"):
        _parse_apply_fill_result(response)


def test_parse_apply_fill_result_rejects_status_invariant_mismatch() -> None:
    response = httpx.Response(
        200,
        json=[
            {
                "outcome": "duplicate",
                "reason": "order_id",
                "committed_trade_id": str(uuid4()),
                "position_action": "deleted",
                "resulting_position": None,
            }
        ],
    )

    with pytest.raises(SupabaseError, match="invalid oms_paper_apply_fill response"):
        _parse_apply_fill_result(response)
