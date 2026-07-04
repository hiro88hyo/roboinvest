from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from oms_paper.__main__ import main
from oms_paper._testing import DEFAULT_TS, make_order_book, make_order_request, make_paper_position
from oms_paper.backtest.writer import write_jsonl, write_positions_json
from trade_contracts.enums import Side, TradingStyle


def _setup_inputs(tmp_path: Path) -> dict[str, Path]:
    book_dir = tmp_path / "in"
    book_dir.mkdir()

    books = [make_order_book(symbol="7203", asks=(("1000", 200),), timestamp=DEFAULT_TS)]
    orders = [
        make_order_request(
            symbol="7203",
            side=Side.BUY,
            quantity=100,
            created_at=DEFAULT_TS + timedelta(seconds=1),
        ),
        make_order_request(
            symbol="9984",
            side=Side.BUY,
            quantity=100,
            created_at=DEFAULT_TS + timedelta(seconds=2),
        ),
    ]

    orders_path = book_dir / "orders.jsonl"
    books_path = book_dir / "books.jsonl"
    write_jsonl(orders, orders_path)
    write_jsonl(books, books_path)
    return {"orders": orders_path, "books": books_path}


def test_cli_backtest_writes_fills_and_positions(tmp_path: Path) -> None:
    paths = _setup_inputs(tmp_path)
    out_dir = tmp_path / "out"
    fills_path = out_dir / "fills.jsonl"
    positions_path = out_dir / "positions.json"
    rejected_path = out_dir / "rejects.jsonl"
    report_path = out_dir / "backtest_report.json"

    rc = main(
        [
            "backtest",
            "--orders",
            str(paths["orders"]),
            "--books",
            str(paths["books"]),
            "--output-fills",
            str(fills_path),
            "--output-positions",
            str(positions_path),
            "--output-rejected",
            str(rejected_path),
        ]
    )
    assert rc == 0
    assert fills_path.exists()
    assert positions_path.exists()
    assert rejected_path.exists()
    assert report_path.exists()

    fills_lines = [ln for ln in fills_path.read_text(encoding="utf-8").splitlines() if ln]
    assert len(fills_lines) == 1  # 7203 だけ約定
    rejected_lines = [ln for ln in rejected_path.read_text(encoding="utf-8").splitlines() if ln]
    assert len(rejected_lines) == 1  # 9984 は no_book
    assert json.loads(rejected_lines[0])["reason"] == "no_book"

    positions = json.loads(positions_path.read_text(encoding="utf-8"))
    assert "7203" in positions
    assert positions["7203"]["quantity"] == 100
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["closed_trade_count"] == 0
    assert report["total_net_pnl"] == "0"


def test_cli_backtest_with_initial_positions(tmp_path: Path) -> None:
    paths = _setup_inputs(tmp_path)
    out_dir = tmp_path / "out"
    fills_path = out_dir / "fills.jsonl"
    positions_path = out_dir / "positions.json"

    seed_path = tmp_path / "seed.json"
    write_positions_json(
        {"7203": make_paper_position(symbol="7203", quantity=200, entry_price=Decimal("900"))},
        seed_path,
    )

    rc = main(
        [
            "backtest",
            "--orders",
            str(paths["orders"]),
            "--books",
            str(paths["books"]),
            "--positions",
            str(seed_path),
            "--output-fills",
            str(fills_path),
            "--output-positions",
            str(positions_path),
        ]
    )
    assert rc == 0
    positions = json.loads(positions_path.read_text(encoding="utf-8"))
    # 既存 200 + 約定 100 = 300
    assert positions["7203"]["quantity"] == 300


def test_cli_backtest_default_holding_type(tmp_path: Path) -> None:
    paths = _setup_inputs(tmp_path)
    out_dir = tmp_path / "out"
    fills_path = out_dir / "fills.jsonl"
    positions_path = out_dir / "positions.json"

    rc = main(
        [
            "backtest",
            "--orders",
            str(paths["orders"]),
            "--books",
            str(paths["books"]),
            "--output-fills",
            str(fills_path),
            "--output-positions",
            str(positions_path),
            "--default-holding-type",
            TradingStyle.SWING.value,
        ]
    )
    assert rc == 0
    positions = json.loads(positions_path.read_text(encoding="utf-8"))
    assert positions["7203"]["holding_type"] == "swing"


def test_cli_backtest_missing_orders_file(tmp_path: Path) -> None:
    rc = main(
        [
            "backtest",
            "--orders",
            str(tmp_path / "missing.jsonl"),
            "--books",
            str(tmp_path / "books.jsonl"),
            "--output-fills",
            str(tmp_path / "fills.jsonl"),
            "--output-positions",
            str(tmp_path / "positions.json"),
        ]
    )
    assert rc == 2


def test_cli_stream_requires_pubsub_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "")
    rc = main(["stream", "--iterations", "0"])
    assert rc == 2


def test_cli_stream_requires_supabase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-dev")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "pubsub:8085")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "")
    rc = main(["stream", "--iterations", "0"])
    assert rc == 2


def test_cli_stream_iterations_zero_with_env_runs_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env が揃っていて --iterations 0 のときは何もせず正常終了する。

    Pub/Sub / Supabase クライアントは ``__aenter__`` 時にネットワーク接続しないため、
    iterations=0 ならハンドシェイクは発生しない。
    """
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-dev")
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "pubsub:8085")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    rc = main(["stream", "--iterations", "0", "--no-closeout"])
    assert rc == 0


def test_cli_opening_swing_exits_requires_supabase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-dev")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "")
    rc = main(["opening-swing-exits", "--book-warmup-batches", "0"])
    assert rc == 2


def test_cli_opening_swing_exits_requires_pubsub_for_book_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    rc = main(["opening-swing-exits"])
    assert rc == 2


def test_cli_opening_swing_exits_dispatches_manual_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    async def _fake_cmd(*, book_warmup_batches: int) -> int:
        calls.append(book_warmup_batches)
        return 7

    monkeypatch.setattr("oms_paper.__main__._run_opening_swing_exits_cmd", _fake_cmd)

    rc = main(["opening-swing-exits", "--book-warmup-batches", "3"])

    assert rc == 7
    assert calls == [3]
