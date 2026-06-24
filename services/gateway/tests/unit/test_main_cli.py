from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from gateway.__main__ import main
from trade_contracts.enums import Action, OrderType, SignalSource, TradeMode, TradingStyle
from trade_contracts.order import OrderRequest
from trade_contracts.risk import KillSwitchState
from trade_contracts.signal import UnifiedTradeSignal


def _signal(
    *,
    symbol: str = "7203",
    price: Decimal | None = None,
    action: Action = Action.BUY,
    stop_loss_price: Decimal | None = Decimal("950"),
    holding_type: TradingStyle = TradingStyle.DAY,
) -> UnifiedTradeSignal:
    return UnifiedTradeSignal(
        signal_id=uuid4(),
        symbol=symbol,
        price=price,
        action=action,
        confidence=0.7,
        signal_source=SignalSource.CONSENSUS,
        holding_type=holding_type,
        stop_loss_price=stop_loss_price,
        created_at=datetime(2026, 4, 23, 9, 0, tzinfo=UTC),
    )


def _write_signals(path: Path, signals: list[UnifiedTradeSignal]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for s in signals:
            f.write(s.model_dump_json())
            f.write("\n")


def _write_state(path: Path, **overrides: object) -> None:
    state = KillSwitchState(
        is_trading_allowed=True,
        trade_mode=TradeMode.LIVE,
        trading_style=TradingStyle.DAY,
        daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"),
        monthly_pnl=Decimal("0"),
        daily_loss_limit=Decimal("10000"),
        weekly_loss_limit=Decimal("30000"),
        monthly_loss_limit=Decimal("100000"),
        updated_at=datetime(2026, 4, 23, 9, 0, tzinfo=UTC),
    )
    # apply overrides via re-construction so we keep types clean
    merged = state.model_copy(update=overrides)
    path.write_text(merged.model_dump_json(), encoding="utf-8")


def test_main_no_args_errors() -> None:
    with pytest.raises(SystemExit):
        main([])


def test_main_unknown_subcommand_rejected() -> None:
    with pytest.raises(SystemExit):
        main(["bogus"])


def test_backtest_missing_input_returns_2(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    _write_state(state_path)
    missing = tmp_path / "nope.jsonl"
    rc = main(
        [
            "backtest",
            "--input",
            str(missing),
            "--state",
            str(state_path),
            "--entry-price",
            "1000",
            "--output-approved",
            str(tmp_path / "out.jsonl"),
        ]
    )
    assert rc == 2


def test_backtest_missing_state_returns_2(tmp_path: Path) -> None:
    input_path = tmp_path / "unified.jsonl"
    _write_signals(input_path, [])
    rc = main(
        [
            "backtest",
            "--input",
            str(input_path),
            "--state",
            str(tmp_path / "nope.json"),
            "--entry-price",
            "1000",
            "--output-approved",
            str(tmp_path / "out.jsonl"),
        ]
    )
    assert rc == 2


def test_backtest_missing_positions_returns_2(tmp_path: Path) -> None:
    input_path = tmp_path / "unified.jsonl"
    state_path = tmp_path / "state.json"
    _write_signals(input_path, [])
    _write_state(state_path)
    rc = main(
        [
            "backtest",
            "--input",
            str(input_path),
            "--state",
            str(state_path),
            "--entry-price",
            "1000",
            "--output-approved",
            str(tmp_path / "out.jsonl"),
            "--positions",
            str(tmp_path / "nope.json"),
        ]
    )
    assert rc == 2


def test_backtest_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPITAL", "1000000")
    input_path = tmp_path / "unified.jsonl"
    state_path = tmp_path / "state.json"
    approved_path = tmp_path / "out" / "orders.jsonl"
    rejected_path = tmp_path / "out" / "rejects.jsonl"

    signals = [
        _signal(symbol="7203", action=Action.BUY, stop_loss_price=Decimal("950")),
        _signal(symbol="9984", action=Action.HOLD, stop_loss_price=None),
    ]
    _write_signals(input_path, signals)
    _write_state(state_path)

    rc = main(
        [
            "backtest",
            "--input",
            str(input_path),
            "--state",
            str(state_path),
            "--entry-price",
            "1000",
            "--output-approved",
            str(approved_path),
            "--output-rejected",
            str(rejected_path),
        ]
    )
    assert rc == 0

    approved = [
        OrderRequest.model_validate_json(line)
        for line in approved_path.read_text().splitlines()
        if line
    ]
    assert len(approved) == 1
    assert approved[0].symbol == "7203"
    assert approved[0].quantity == 400
    assert approved[0].order_type is OrderType.LIMIT
    assert approved[0].limit_price == Decimal("1000")
    assert approved[0].trade_mode is TradeMode.LIVE

    rejected_lines = [line for line in rejected_path.read_text().splitlines() if line]
    assert len(rejected_lines) == 1
    assert '"reason":"action_hold"' in rejected_lines[0]


def test_backtest_uses_signal_price_when_entry_price_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAPITAL", "1000000")
    input_path = tmp_path / "unified.jsonl"
    state_path = tmp_path / "state.json"
    approved_path = tmp_path / "out" / "orders.jsonl"

    _write_signals(
        input_path,
        [_signal(symbol="7203", price=Decimal("2000"), stop_loss_price=Decimal("1900"))],
    )
    _write_state(state_path)

    rc = main(
        [
            "backtest",
            "--input",
            str(input_path),
            "--state",
            str(state_path),
            "--output-approved",
            str(approved_path),
        ]
    )
    assert rc == 0

    [order] = [
        OrderRequest.model_validate_json(line)
        for line in approved_path.read_text().splitlines()
        if line
    ]
    assert order.limit_price == Decimal("2000")
    assert order.quantity == 200


def test_backtest_applies_buy_limit_offset_ticks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAPITAL", "1000000")
    input_path = tmp_path / "unified.jsonl"
    state_path = tmp_path / "state.json"
    approved_path = tmp_path / "out" / "orders.jsonl"

    _write_signals(
        input_path,
        [_signal(symbol="7203", price=Decimal("2000"), stop_loss_price=Decimal("1900"))],
    )
    _write_state(state_path)

    rc = main(
        [
            "backtest",
            "--input",
            str(input_path),
            "--state",
            str(state_path),
            "--output-approved",
            str(approved_path),
            "--buy-limit-offset-ticks",
            "3",
        ]
    )
    assert rc == 0

    [order] = [
        OrderRequest.model_validate_json(line)
        for line in approved_path.read_text().splitlines()
        if line
    ]
    assert order.limit_price == Decimal("2003")


def test_backtest_without_rejected_output_skips_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAPITAL", "1000000")
    input_path = tmp_path / "unified.jsonl"
    state_path = tmp_path / "state.json"
    approved_path = tmp_path / "out" / "orders.jsonl"

    _write_signals(input_path, [_signal(action=Action.HOLD, stop_loss_price=None)])
    _write_state(state_path)

    rc = main(
        [
            "backtest",
            "--input",
            str(input_path),
            "--state",
            str(state_path),
            "--entry-price",
            "1000",
            "--output-approved",
            str(approved_path),
        ]
    )
    assert rc == 0
    # approved file exists but is empty (HOLD rejected, no rejection file requested)
    assert approved_path.exists()
    assert approved_path.read_text() == ""


def test_backtest_positions_allow_sell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPITAL", "1000000")
    input_path = tmp_path / "unified.jsonl"
    state_path = tmp_path / "state.json"
    positions_path = tmp_path / "positions.json"
    approved_path = tmp_path / "orders.jsonl"

    _write_signals(input_path, [_signal(symbol="7203", action=Action.SELL, stop_loss_price=None)])
    _write_state(state_path)
    positions_path.write_text(json.dumps({"7203": 200}), encoding="utf-8")

    rc = main(
        [
            "backtest",
            "--input",
            str(input_path),
            "--state",
            str(state_path),
            "--entry-price",
            "1000",
            "--output-approved",
            str(approved_path),
            "--positions",
            str(positions_path),
        ]
    )
    assert rc == 0
    [order] = [
        OrderRequest.model_validate_json(line)
        for line in approved_path.read_text().splitlines()
        if line
    ]
    assert order.quantity == 200


def test_backtest_capital_cli_overrides_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # env CAPITAL=100,000 alone would yield qty = 40 < min_lot → rejected.
    # --capital 1,000,000 via CLI override lifts it to 400.
    monkeypatch.setenv("CAPITAL", "100000")
    input_path = tmp_path / "unified.jsonl"
    state_path = tmp_path / "state.json"
    approved_path = tmp_path / "orders.jsonl"

    _write_signals(input_path, [_signal(symbol="7203", stop_loss_price=Decimal("950"))])
    _write_state(state_path)

    rc = main(
        [
            "backtest",
            "--input",
            str(input_path),
            "--state",
            str(state_path),
            "--entry-price",
            "1000",
            "--output-approved",
            str(approved_path),
            "--capital",
            "1000000",
        ]
    )
    assert rc == 0
    [order] = [
        OrderRequest.model_validate_json(line)
        for line in approved_path.read_text().splitlines()
        if line
    ]
    assert order.quantity == 400


def test_backtest_max_notional_per_order_cli_caps_quantity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAPITAL", "1000000")
    input_path = tmp_path / "unified.jsonl"
    state_path = tmp_path / "state.json"
    approved_path = tmp_path / "orders.jsonl"

    _write_signals(
        input_path,
        [
            _signal(
                symbol="7203",
                price=Decimal("1000"),
                stop_loss_price=Decimal("998"),
            )
        ],
    )
    _write_state(state_path)

    rc = main(
        [
            "backtest",
            "--input",
            str(input_path),
            "--state",
            str(state_path),
            "--output-approved",
            str(approved_path),
            "--max-notional-per-order-pct",
            "0.20",
        ]
    )
    assert rc == 0
    [order] = [
        OrderRequest.model_validate_json(line)
        for line in approved_path.read_text().splitlines()
        if line
    ]
    assert order.quantity == 200


def test_backtest_positions_file_must_be_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAPITAL", "1000000")
    input_path = tmp_path / "unified.jsonl"
    state_path = tmp_path / "state.json"
    positions_path = tmp_path / "positions.json"
    approved_path = tmp_path / "orders.jsonl"

    _write_signals(input_path, [_signal()])
    _write_state(state_path)
    positions_path.write_text(json.dumps(["7203", 100]), encoding="utf-8")

    with pytest.raises(ValueError):
        main(
            [
                "backtest",
                "--input",
                str(input_path),
                "--state",
                str(state_path),
                "--entry-price",
                "1000",
                "--output-approved",
                str(approved_path),
                "--positions",
                str(positions_path),
            ]
        )


def test_stream_requires_supabase_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-dev")

    rc = main(["stream", "--iterations", "0"])
    assert rc == 2


def test_stream_requires_pubsub_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "")

    rc = main(["stream", "--iterations", "0"])
    assert rc == 2
