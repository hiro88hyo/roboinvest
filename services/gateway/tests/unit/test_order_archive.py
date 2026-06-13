from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from gateway.order_archive import OrderArchiveWriter
from trade_contracts.enums import OrderType, Side, SignalSource, TradeMode
from trade_contracts.order import OrderRequest


def _order(symbol: str) -> OrderRequest:
    return _order_at(symbol, datetime(2026, 4, 20, 1, 0, tzinfo=UTC))


def _order_at(symbol: str, created_at: datetime) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        side=Side.BUY,
        quantity=100,
        order_type=OrderType.MARKET,
        trade_mode=TradeMode.PAPER,
        signal_source=SignalSource.CONSENSUS,
        created_at=created_at,
    )


def test_order_archive_appends_order_request_jsonl(tmp_path: Path) -> None:
    writer = OrderArchiveWriter(tmp_path)

    path = writer.record_order(_order("7203"))
    writer.record_order(_order("9432"))

    assert path == tmp_path / "trade_mode=paper" / "date=2026-04-20" / "orders.jsonl"
    rows = path.read_text(encoding="utf-8").splitlines()
    assert [OrderRequest.model_validate_json(row).symbol for row in rows] == ["7203", "9432"]


def test_order_archive_partitions_by_jst_date(tmp_path: Path) -> None:
    writer = OrderArchiveWriter(tmp_path)

    path = writer.record_order(_order_at("7203", datetime(2026, 4, 19, 15, 30, tzinfo=UTC)))

    assert path == tmp_path / "trade_mode=paper" / "date=2026-04-20" / "orders.jsonl"
