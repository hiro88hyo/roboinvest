from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from oms_paper._testing import make_order_book, make_order_request, make_paper_position
from oms_paper.backtest.reader import (
    iter_order_books,
    iter_order_requests,
    read_positions_json,
)
from oms_paper.backtest.runner import NoFillRecord
from oms_paper.models import FillResult, PaperFillRecord, PaperPosition
from oms_paper.position_updater import build_fill_record
from trade_contracts.enums import Side
from trade_contracts.market import OrderBookSnapshot
from trade_contracts.order import OrderRequest

from oms_paper.backtest.writer import write_jsonl, write_positions_json  # isort: skip


def test_order_request_jsonl_roundtrip(tmp_path: Path) -> None:
    orders = [
        make_order_request(symbol="7203", side=Side.BUY, quantity=100),
        make_order_request(symbol="9984", side=Side.SELL, quantity=200),
    ]
    out = tmp_path / "orders.jsonl"
    n = write_jsonl(orders, out)
    assert n == 2
    loaded = list(iter_order_requests(out))
    assert len(loaded) == 2
    assert all(isinstance(o, OrderRequest) for o in loaded)
    assert loaded[0].symbol == "7203"
    assert loaded[1].side is Side.SELL


def test_book_jsonl_roundtrip(tmp_path: Path) -> None:
    books = [make_order_book(symbol="7203"), make_order_book(symbol="9984")]
    out = tmp_path / "books.jsonl"
    write_jsonl(books, out)
    loaded = list(iter_order_books(out))
    assert len(loaded) == 2
    assert all(isinstance(b, OrderBookSnapshot) for b in loaded)


def test_iter_skips_blank_lines(tmp_path: Path) -> None:
    order = make_order_request()
    out = tmp_path / "with-blanks.jsonl"
    out.write_text(
        "\n" + order.model_dump_json() + "\n\n" + order.model_dump_json() + "\n",
        encoding="utf-8",
    )
    loaded = list(iter_order_requests(out))
    assert len(loaded) == 2


def test_positions_json_roundtrip(tmp_path: Path) -> None:
    positions = {
        "7203": make_paper_position(symbol="7203", quantity=100, entry_price=Decimal("1000")),
        "9984": make_paper_position(symbol="9984", quantity=200, entry_price=Decimal("8000")),
    }
    out = tmp_path / "positions.json"
    write_positions_json(positions, out)
    loaded = read_positions_json(out)
    assert set(loaded) == {"7203", "9984"}
    assert loaded["7203"].entry_price == Decimal("1000")
    assert loaded["9984"].quantity == 200
    assert isinstance(loaded["7203"], PaperPosition)


def test_positions_json_sorted_by_symbol(tmp_path: Path) -> None:
    positions = {
        "Z": make_paper_position(symbol="Z"),
        "A": make_paper_position(symbol="A"),
        "M": make_paper_position(symbol="M"),
    }
    out = tmp_path / "positions.json"
    write_positions_json(positions, out)
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert list(raw.keys()) == ["A", "M", "Z"]


def test_read_positions_rejects_non_object(tmp_path: Path) -> None:
    out = tmp_path / "bad.json"
    out.write_text("[]", encoding="utf-8")
    try:
        read_positions_json(out)
    except ValueError as exc:
        assert "JSON object" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_read_positions_empty_object(tmp_path: Path) -> None:
    out = tmp_path / "empty.json"
    out.write_text("{}", encoding="utf-8")
    assert read_positions_json(out) == {}


def test_no_fill_record_serializes_with_jsonl(tmp_path: Path) -> None:
    rec = NoFillRecord(
        unified_signal_id=make_order_request().unified_signal_id,
        symbol="7203",
        side=Side.BUY,
        quantity=100,
        reason="no_book",
        created_at=datetime(2026, 4, 25, 9, 0, tzinfo=UTC),
    )
    out = tmp_path / "rejects.jsonl"
    write_jsonl([rec], out)
    line = out.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["reason"] == "no_book"
    assert parsed["symbol"] == "7203"


def test_paper_fill_record_serializes_with_jsonl(tmp_path: Path) -> None:
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100)
    fill = FillResult(filled_quantity=100, fill_price=Decimal("1000"), reason="filled")
    rec = build_fill_record(
        order=order,
        fill=fill,
        executed_at=datetime(2026, 4, 25, 9, 0, tzinfo=UTC),
    )
    assert isinstance(rec, PaperFillRecord)
    out = tmp_path / "fills.jsonl"
    write_jsonl([rec], out)
    parsed = json.loads(out.read_text(encoding="utf-8").strip())
    assert parsed["price"] == "1000"
    assert parsed["quantity"] == 100
