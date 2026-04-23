from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from aggregator.__main__ import main
from trade_contracts.enums import Action, SignalSource
from trade_contracts.signal import StrategySignal, UnifiedTradeSignal


def _write_jsonl(path: Path, signals: list[StrategySignal]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for s in signals:
            f.write(s.model_dump_json())
            f.write("\n")


def _sig(
    *,
    source: SignalSource,
    symbol: str,
    action: Action,
    confidence: float,
    created_at: datetime,
) -> StrategySignal:
    return StrategySignal(
        signal_id=uuid4(),
        source=source,
        symbol=symbol,
        action=action,
        confidence=confidence,
        created_at=created_at,
    )


def test_main_no_args_errors() -> None:
    with pytest.raises(SystemExit):
        main([])


def test_main_stream_missing_supabase_env_returns_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "test-project")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    assert main(["stream", "--iterations", "0"]) == 2


def test_main_stream_missing_pubsub_project_returns_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPABASE_URL", "http://localhost:54321")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "dummy")
    monkeypatch.delenv("PUBSUB_PROJECT_ID", raising=False)
    assert main(["stream", "--iterations", "0"]) == 2


def test_main_unknown_subcommand_rejected() -> None:
    with pytest.raises(SystemExit):
        main(["bogus"])


def test_backtest_missing_input_a_returns_2(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.jsonl"
    rc = main(["backtest", "--input-a", str(missing), "--output", str(tmp_path / "out.jsonl")])
    assert rc == 2


def test_backtest_missing_input_b_returns_2(tmp_path: Path) -> None:
    a_path = tmp_path / "a.jsonl"
    _write_jsonl(a_path, [])
    missing = tmp_path / "nope.jsonl"
    rc = main(
        [
            "backtest",
            "--input-a",
            str(a_path),
            "--input-b",
            str(missing),
            "--output",
            str(tmp_path / "out.jsonl"),
        ]
    )
    assert rc == 2


def test_backtest_rule_only_passthrough(tmp_path: Path) -> None:
    a_path = tmp_path / "a.jsonl"
    out_path = tmp_path / "unified.jsonl"
    ts = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    _write_jsonl(
        a_path,
        [
            _sig(
                source=SignalSource.RULE,
                symbol="7203",
                action=Action.BUY,
                confidence=0.8,
                created_at=ts,
            ),
        ],
    )

    rc = main(
        [
            "backtest",
            "--input-a",
            str(a_path),
            "--output",
            str(out_path),
            "--bucket-ms",
            "1000",
        ]
    )
    assert rc == 0

    rows = [
        UnifiedTradeSignal.model_validate_json(line)
        for line in out_path.read_text().splitlines()
        if line
    ]
    assert len(rows) == 1
    assert rows[0].signal_source is SignalSource.RULE
    assert rows[0].action is Action.BUY


def test_backtest_agreement_writes_consensus(tmp_path: Path) -> None:
    a_path = tmp_path / "a.jsonl"
    b_path = tmp_path / "b.jsonl"
    out_path = tmp_path / "unified.jsonl"
    ts = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    _write_jsonl(
        a_path,
        [
            _sig(
                source=SignalSource.RULE,
                symbol="7203",
                action=Action.BUY,
                confidence=0.6,
                created_at=ts,
            ),
        ],
    )
    _write_jsonl(
        b_path,
        [
            _sig(
                source=SignalSource.AI,
                symbol="7203",
                action=Action.BUY,
                confidence=0.8,
                created_at=ts,
            ),
        ],
    )

    rc = main(
        [
            "backtest",
            "--input-a",
            str(a_path),
            "--input-b",
            str(b_path),
            "--output",
            str(out_path),
            "--bucket-ms",
            "1000",
        ]
    )
    assert rc == 0

    rows = [
        UnifiedTradeSignal.model_validate_json(line)
        for line in out_path.read_text().splitlines()
        if line
    ]
    assert len(rows) == 1
    assert rows[0].signal_source is SignalSource.CONSENSUS
    assert rows[0].confidence == pytest.approx(0.7)
