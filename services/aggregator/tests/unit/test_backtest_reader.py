from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from aggregator.backtest.reader import iter_signals
from pydantic import ValidationError
from trade_contracts.enums import Action, SignalSource
from trade_contracts.signal import StrategySignal


def test_iter_signals_skips_blank_lines(tmp_path: Path) -> None:
    sig = StrategySignal(
        signal_id=uuid4(),
        source=SignalSource.RULE,
        symbol="7203",
        action=Action.BUY,
        confidence=0.8,
        created_at=datetime(2026, 4, 20, 9, 0, tzinfo=UTC),
    )
    path = tmp_path / "signals.jsonl"
    path.write_text(f"\n{sig.model_dump_json()}\n\n")
    rows = list(iter_signals(path))
    assert len(rows) == 1
    assert rows[0].signal_id == sig.signal_id


def test_iter_signals_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "signals.jsonl"
    path.write_text("")
    assert list(iter_signals(path)) == []


def test_iter_signals_malformed_raises(tmp_path: Path) -> None:
    path = tmp_path / "signals.jsonl"
    path.write_text("{ not json\n")
    with pytest.raises(ValidationError):
        list(iter_signals(path))
