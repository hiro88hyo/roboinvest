from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from strategy_ai.backtest.writer import write_jsonl
from trade_contracts.enums import Action, SignalSource
from trade_contracts.signal import StrategySignal


def _signal(symbol: str = "7203") -> StrategySignal:
    return StrategySignal(
        signal_id=uuid4(),
        source=SignalSource.AI,
        symbol=symbol,
        action=Action.BUY,
        confidence=0.5,
        reasoning="x",
        created_at=datetime(2026, 4, 27, 9, 0, tzinfo=UTC),
    )


def test_write_jsonl_creates_dirs_and_writes_rows(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "signals.jsonl"
    rows = [_signal("7203"), _signal("9984")]

    written = write_jsonl(rows, out)

    assert written == 2
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert '"symbol":"7203"' in lines[0]
    assert '"symbol":"9984"' in lines[1]


def test_write_jsonl_overwrites(tmp_path: Path) -> None:
    out = tmp_path / "signals.jsonl"
    out.write_text("garbage\n", encoding="utf-8")

    write_jsonl([_signal()], out)

    assert out.read_text(encoding="utf-8").count("\n") == 1
