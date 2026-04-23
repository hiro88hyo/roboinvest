from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from gateway.backtest import iter_unified_signals
from pydantic import ValidationError
from trade_contracts.enums import Action, SignalSource, TradingStyle
from trade_contracts.signal import UnifiedTradeSignal


def _sig(*, symbol: str = "7203", action: Action = Action.BUY) -> UnifiedTradeSignal:
    return UnifiedTradeSignal(
        signal_id=uuid4(),
        symbol=symbol,
        action=action,
        confidence=0.7,
        signal_source=SignalSource.CONSENSUS,
        holding_type=TradingStyle.DAY,
        created_at=datetime(2026, 4, 23, 9, 0, tzinfo=UTC),
    )


def test_reader_reads_multiple_rows(tmp_path: Path) -> None:
    path = tmp_path / "unified.jsonl"
    rows = [_sig(symbol="7203"), _sig(symbol="9984"), _sig(symbol="6758")]
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(r.model_dump_json())
            f.write("\n")

    result = list(iter_unified_signals(path))
    assert [r.symbol for r in result] == ["7203", "9984", "6758"]


def test_reader_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "unified.jsonl"
    row = _sig()
    path.write_text(f"\n{row.model_dump_json()}\n\n", encoding="utf-8")

    result = list(iter_unified_signals(path))
    assert len(result) == 1
    assert result[0].signal_id == row.signal_id


def test_reader_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert list(iter_unified_signals(path)) == []


def test_reader_malformed_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"not": "a signal"}\n', encoding="utf-8")
    with pytest.raises(ValidationError):
        list(iter_unified_signals(path))
