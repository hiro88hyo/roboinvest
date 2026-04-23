from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from gateway.backtest import write_jsonl
from gateway.backtest.runner import RejectionRecord
from trade_contracts.enums import Action, OrderType, Side, SignalSource, TradeMode
from trade_contracts.order import OrderRequest


def _order() -> OrderRequest:
    return OrderRequest(
        unified_signal_id=uuid4(),
        symbol="7203",
        side=Side.BUY,
        quantity=100,
        order_type=OrderType.MARKET,
        trade_mode=TradeMode.LIVE,
        signal_source=SignalSource.CONSENSUS,
        created_at=datetime(2026, 4, 23, 10, 0, tzinfo=UTC),
    )


def _rejection() -> RejectionRecord:
    return RejectionRecord(
        unified_signal_id=uuid4(),
        symbol="7203",
        action=Action.BUY,
        reason="daily_loss_limit",
        created_at=datetime(2026, 4, 23, 10, 0, tzinfo=UTC),
    )


def test_writer_writes_orders(tmp_path: Path) -> None:
    path = tmp_path / "orders.jsonl"
    count = write_jsonl([_order(), _order()], path)
    assert count == 2
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    roundtrip = OrderRequest.model_validate_json(lines[0])
    assert roundtrip.symbol == "7203"


def test_writer_writes_rejections(tmp_path: Path) -> None:
    path = tmp_path / "rejects.jsonl"
    count = write_jsonl([_rejection()], path)
    assert count == 1
    roundtrip = RejectionRecord.model_validate_json(path.read_text().strip())
    assert roundtrip.reason == "daily_loss_limit"


def test_writer_creates_parent_dir(tmp_path: Path) -> None:
    path = tmp_path / "deeply" / "nested" / "orders.jsonl"
    write_jsonl([_order()], path)
    assert path.exists()


def test_writer_overwrites_existing(tmp_path: Path) -> None:
    path = tmp_path / "orders.jsonl"
    write_jsonl([_order(), _order()], path)
    write_jsonl([_order()], path)  # overwrite with 1 row
    assert len(path.read_text().splitlines()) == 1


def test_writer_empty_iterable(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    count = write_jsonl([], path)
    assert count == 0
    assert path.read_text() == ""
