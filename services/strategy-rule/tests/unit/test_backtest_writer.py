from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from strategy_rule.backtest.writer import write_jsonl
from trade_contracts.enums import Action, SignalSource
from trade_contracts.signal import StrategySignal


def _signal(symbol: str = "7203") -> StrategySignal:
    return StrategySignal(
        source=SignalSource.RULE,
        symbol=symbol,
        action=Action.BUY,
        confidence=0.7,
        reasoning="test",
        created_at=datetime(2026, 4, 20, 9, 0, tzinfo=UTC),
    )


def test_writes_one_line_per_signal(tmp_path: Path) -> None:
    signals = [_signal("7203"), _signal("9984")]
    path = tmp_path / "out" / "signals.jsonl"
    n = write_jsonl(signals, path)
    assert n == 2
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    parsed = [StrategySignal.model_validate_json(line) for line in lines]
    assert [s.symbol for s in parsed] == ["7203", "9984"]


def test_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "nested" / "signals.jsonl"
    write_jsonl([_signal()], path)
    assert path.exists()


def test_overwrites_existing(tmp_path: Path) -> None:
    path = tmp_path / "signals.jsonl"
    path.write_text("garbage\n", encoding="utf-8")
    write_jsonl([_signal()], path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_empty_iterable_writes_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "signals.jsonl"
    n = write_jsonl([], path)
    assert n == 0
    assert path.read_text(encoding="utf-8") == ""
