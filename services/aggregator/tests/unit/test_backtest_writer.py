from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from trade_contracts.enums import Action, SignalSource, TradingStyle
from trade_contracts.signal import UnifiedTradeSignal

from aggregator.backtest.writer import write_jsonl


def _unified(symbol: str = "7203") -> UnifiedTradeSignal:
    return UnifiedTradeSignal(
        symbol=symbol,
        action=Action.BUY,
        confidence=0.7,
        signal_source=SignalSource.CONSENSUS,
        holding_type=TradingStyle.DAY,
        created_at=datetime(2026, 4, 20, 9, 0, tzinfo=UTC),
    )


def test_write_jsonl_creates_parent_dirs(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "unified.jsonl"
    count = write_jsonl([_unified(), _unified("9984")], out)
    assert count == 2
    assert out.exists()
    lines = out.read_text().splitlines()
    assert len(lines) == 2
    # Round-trip
    loaded = [UnifiedTradeSignal.model_validate_json(line) for line in lines]
    assert {u.symbol for u in loaded} == {"7203", "9984"}


def test_write_jsonl_overwrites_existing(tmp_path: Path) -> None:
    out = tmp_path / "unified.jsonl"
    out.write_text("old content\n")
    count = write_jsonl([_unified()], out)
    assert count == 1
    assert out.read_text().count("\n") == 1


def test_write_jsonl_empty(tmp_path: Path) -> None:
    out = tmp_path / "unified.jsonl"
    count = write_jsonl([], out)
    assert count == 0
    assert out.read_text() == ""
