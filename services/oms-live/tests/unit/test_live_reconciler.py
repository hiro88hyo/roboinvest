"""reconciler (oms-live) の単体テスト。

純関数 ``parse_kabu_position`` / ``compute_position_diff`` /
``build_imported_position`` と、I/O 適用層 ``apply_reconcile_actions`` を検証する。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from oms_live._testing import make_live_position
from oms_live.clients.supabase import SupabaseClient
from oms_live.reconciler import (
    KabuPositionSnapshot,
    QuantityMismatch,
    apply_reconcile_actions,
    build_imported_position,
    compute_position_diff,
    parse_kabu_position,
    parse_kabu_positions,
)
from trade_contracts.enums import TradingStyle


def _kabu_row(
    *,
    symbol: str = "9432",
    side: str = "2",
    leaves_qty: int = 2000,
    hold_qty: int = 0,
    price: float = 153.0,
) -> dict[str, Any]:
    return {
        "Symbol": symbol,
        "Exchange": 1,
        "AccountType": 4,
        "Side": side,
        "LeavesQty": leaves_qty,
        "HoldQty": hold_qty,
        "Price": price,
        "CurrentPrice": price - 1.5,
        "Valuation": (leaves_qty + hold_qty) * (price - 1.5),
        "ProfitLoss": -3000.0,
    }


# ----- parse_kabu_position --------------------------------------------------


def test_parse_kabu_position_buy_ntt_2000() -> None:
    snap = parse_kabu_position(_kabu_row())
    assert snap == KabuPositionSnapshot(
        symbol="9432", quantity=2000, average_price=Decimal("153.0")
    )


def test_parse_kabu_position_sums_leaves_and_hold() -> None:
    snap = parse_kabu_position(_kabu_row(leaves_qty=1500, hold_qty=500))
    assert snap is not None
    assert snap.quantity == 2000


def test_parse_kabu_position_skips_sell_side() -> None:
    assert parse_kabu_position(_kabu_row(side="1")) is None


def test_parse_kabu_position_skips_zero_quantity() -> None:
    assert parse_kabu_position(_kabu_row(leaves_qty=0, hold_qty=0)) is None


def test_parse_kabu_position_skips_missing_price() -> None:
    row = _kabu_row()
    del row["Price"]
    assert parse_kabu_position(row) is None


def test_parse_kabu_position_skips_when_required_field_missing() -> None:
    # Symbol が無いと KeyError → None
    row = _kabu_row()
    del row["Symbol"]
    assert parse_kabu_position(row) is None


def test_parse_kabu_position_uses_string_decimal_for_float_precision() -> None:
    # float 由来の値が Decimal にそのまま渡らないことを確認 (str 経由)
    snap = parse_kabu_position(_kabu_row(price=151.45))
    assert snap is not None
    assert snap.average_price == Decimal("151.45")


def test_parse_kabu_positions_filters_none_rows() -> None:
    rows = [_kabu_row(symbol="9432"), _kabu_row(symbol="7203", side="1"), _kabu_row(symbol="9984")]
    out = parse_kabu_positions(rows)
    assert [p.symbol for p in out] == ["9432", "9984"]


# ----- compute_position_diff -----------------------------------------------


def _snap(symbol: str, qty: int, price: str = "1000") -> KabuPositionSnapshot:
    return KabuPositionSnapshot(symbol=symbol, quantity=qty, average_price=Decimal(price))


def test_compute_diff_all_matched() -> None:
    actions = compute_position_diff(
        [_snap("7203", 100), _snap("9432", 2000)],
        [
            make_live_position(symbol="7203", quantity=100),
            make_live_position(symbol="9432", quantity=2000),
        ],
    )
    assert set(actions.matched) == {"7203", "9432"}
    assert actions.to_import == ()
    assert actions.quantity_mismatches == ()
    assert actions.supabase_orphans == ()
    assert actions.has_drift is False


def test_compute_diff_kabu_only_to_import() -> None:
    actions = compute_position_diff(
        [_snap("9432", 2000, "153.0")],
        [],
    )
    assert len(actions.to_import) == 1
    assert actions.to_import[0].symbol == "9432"
    assert actions.matched == ()
    assert actions.has_drift is True


def test_compute_diff_supabase_only_orphan() -> None:
    actions = compute_position_diff(
        [],
        [make_live_position(symbol="7203", quantity=100)],
    )
    assert len(actions.supabase_orphans) == 1
    assert actions.supabase_orphans[0].symbol == "7203"
    assert actions.to_import == ()
    assert actions.has_drift is True


def test_compute_diff_quantity_mismatch_recorded() -> None:
    actions = compute_position_diff(
        [_snap("7203", 200, "1010")],
        [make_live_position(symbol="7203", quantity=100, entry_price=Decimal("1000"))],
    )
    assert actions.matched == ()
    assert actions.to_import == ()
    assert actions.quantity_mismatches == (
        QuantityMismatch(
            symbol="7203",
            kabu_quantity=200,
            supabase_quantity=100,
            kabu_average_price=Decimal("1010"),
            supabase_entry_price=Decimal("1000"),
        ),
    )


def test_compute_diff_mixed_buckets() -> None:
    actions = compute_position_diff(
        [_snap("7203", 100), _snap("9432", 2000), _snap("9984", 50)],
        [
            make_live_position(symbol="7203", quantity=100),  # matched
            make_live_position(symbol="9984", quantity=70),  # mismatch
            make_live_position(symbol="6758", quantity=10),  # supabase orphan
        ],
    )
    assert set(actions.matched) == {"7203"}
    assert {p.symbol for p in actions.to_import} == {"9432"}
    assert {m.symbol for m in actions.quantity_mismatches} == {"9984"}
    assert {p.symbol for p in actions.supabase_orphans} == {"6758"}
    assert actions.has_drift is True


# ----- build_imported_position ---------------------------------------------


def test_build_imported_position_defaults_to_swing_no_thresholds() -> None:
    now = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    snap = _snap("9432", 2000, "153.0")
    position = build_imported_position(snap, now=now)
    assert position.symbol == "9432"
    assert position.quantity == 2000
    assert position.entry_price == Decimal("153.0")
    assert position.holding_type is TradingStyle.SWING
    assert position.target_price is None
    assert position.stop_loss_price is None
    assert position.max_hold_days is None
    assert position.trailing_stop_pct is None
    assert position.opened_at == now


def test_build_imported_position_respects_holding_type_override() -> None:
    now = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    position = build_imported_position(_snap("7203", 100), now=now, holding_type=TradingStyle.DAY)
    assert position.holding_type is TradingStyle.DAY


# ----- apply_reconcile_actions (I/O 層) ------------------------------------


def _make_supabase_client(
    handler: httpx.MockTransport,
) -> SupabaseClient:
    return SupabaseClient(
        url="http://localhost:54321",
        secret_key="dummy",
        timeout_seconds=5.0,
        transport=handler,
    )


@pytest.mark.asyncio
async def test_apply_dry_run_does_not_insert(caplog: pytest.LogCaptureFixture) -> None:
    """dry-run (apply_imports=False) では Supabase を一切叩かない。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500, text="should not be called")

    actions = compute_position_diff(
        [_snap("9432", 2000)],
        [make_live_position(symbol="6758", quantity=10)],
    )
    async with _make_supabase_client(httpx.MockTransport(handler)) as supabase:
        with caplog.at_level("WARNING"):
            inserted = await apply_reconcile_actions(
                actions,
                supabase,
                now=datetime(2026, 5, 7, tzinfo=UTC),
                apply_imports=False,
            )
    assert inserted == 0
    assert calls == []
    # warning は to_import / orphan の両方で出る
    messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("kabu position not in supabase" in m for m in messages)
    assert any("supabase position not in kabu" in m for m in messages)


@pytest.mark.asyncio
async def test_apply_imports_inserts_into_positions() -> None:
    # supabase_client.insert_live_position は POST に [{...}] を JSON で送る
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/rest/v1/positions"
        body = json.loads(request.content)
        assert isinstance(body, list) and len(body) == 1
        captured.append(body[0])
        return httpx.Response(201, headers={"Content-Type": "application/json"})

    actions = compute_position_diff(
        [_snap("9432", 2000, "153.0"), _snap("7203", 100, "2350")],
        [],
    )
    async with _make_supabase_client(httpx.MockTransport(handler)) as supabase:
        inserted = await apply_reconcile_actions(
            actions,
            supabase,
            now=datetime(2026, 5, 7, 14, 0, tzinfo=UTC),
            apply_imports=True,
        )
    assert inserted == 2
    inserted_symbols = sorted(row["symbol"] for row in captured)
    assert inserted_symbols == ["7203", "9432"]
    for row in captured:
        assert row["trade_type"] == "live"
        assert row["side"] == "LONG"
        assert row["holding_type"] == "swing"


@pytest.mark.asyncio
async def test_apply_imports_does_not_touch_orphans_or_mismatches(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """--apply 指定時でも quantity_mismatch / orphan は warning のみで Supabase を触らない。"""
    posts: list[httpx.Request] = []
    deletes: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request)
            return httpx.Response(201)
        if request.method == "DELETE":
            deletes.append(request)
            return httpx.Response(500, text="MUST NOT DELETE")
        if request.method == "PATCH":
            deletes.append(request)
            return httpx.Response(500, text="MUST NOT PATCH")
        return httpx.Response(500, text=f"unexpected {request.method}")

    actions = compute_position_diff(
        # to_import なし、mismatch と orphan だけ
        [_snap("7203", 200, "1010")],
        [
            make_live_position(symbol="7203", quantity=100, entry_price=Decimal("1000")),
            make_live_position(symbol="6758", quantity=10),
        ],
    )
    async with _make_supabase_client(httpx.MockTransport(handler)) as supabase:
        with caplog.at_level("WARNING"):
            inserted = await apply_reconcile_actions(
                actions,
                supabase,
                now=datetime(2026, 5, 7, tzinfo=UTC),
                apply_imports=True,
            )
    assert inserted == 0
    assert posts == []
    assert deletes == []
    messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("position quantity mismatch" in m for m in messages)
    assert any("supabase position not in kabu" in m for m in messages)
