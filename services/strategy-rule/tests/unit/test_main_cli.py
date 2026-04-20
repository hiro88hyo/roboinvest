from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from strategy_rule.__main__ import _build_parser, _run_backtest_cmd, _run_stream_cmd
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal


def _write_features(path: Path, features: list[ProcessedFeatures]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f.model_dump_json() for f in features) + "\n", encoding="utf-8")


def test_parser_requires_subcommand() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_requires_input() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["backtest"])


def test_parser_parses_paths(tmp_path: Path) -> None:
    parser = _build_parser()
    args = parser.parse_args(
        ["backtest", "--input", str(tmp_path / "in.jsonl"), "--output", str(tmp_path / "out.jsonl")]
    )
    assert args.command == "backtest"
    assert args.input_path == tmp_path / "in.jsonl"
    assert args.output_path == tmp_path / "out.jsonl"


def test_run_backtest_writes_signals_for_sma_cross(
    tmp_path: Path,
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    base = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    feats = [
        features_factory(
            timestamp=base,
            price=Decimal("1000"),
            sma_short=Decimal("99"),
            sma_long=Decimal("100"),
        ),
        features_factory(
            timestamp=base + timedelta(minutes=1),
            price=Decimal("1000"),
            sma_short=Decimal("120"),
            sma_long=Decimal("100"),
        ),
    ]
    in_path = tmp_path / "features.jsonl"
    out_path = tmp_path / "signals.jsonl"
    _write_features(in_path, feats)

    rc = _run_backtest_cmd(input_path=in_path, output_path=out_path)
    assert rc == 0
    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 1
    signal = StrategySignal.model_validate_json(lines[0])
    assert signal.symbol == "7203"


def test_run_backtest_returns_2_when_input_missing(tmp_path: Path) -> None:
    rc = _run_backtest_cmd(input_path=tmp_path / "nope.jsonl", output_path=tmp_path / "out.jsonl")
    assert rc == 2


def test_run_backtest_returns_2_when_no_strategies_enabled(
    tmp_path: Path,
    features_factory: Callable[..., ProcessedFeatures],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    in_path = tmp_path / "features.jsonl"
    out_path = tmp_path / "signals.jsonl"
    _write_features(in_path, [features_factory()])
    monkeypatch.setenv("STRATEGIES_ENABLED", "")

    rc = _run_backtest_cmd(input_path=in_path, output_path=out_path)
    assert rc == 2
    assert not out_path.exists()


def test_parser_parses_stream_iterations() -> None:
    parser = _build_parser()
    args = parser.parse_args(["stream", "--iterations", "3"])
    assert args.command == "stream"
    assert args.iterations == 3


async def test_run_stream_cmd_returns_2_when_supabase_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRATEGIES_ENABLED", "rsi_threshold")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-dev")
    rc = await _run_stream_cmd(iterations=1)
    assert rc == 2


async def test_run_stream_cmd_returns_2_when_pubsub_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRATEGIES_ENABLED", "rsi_threshold")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "")
    rc = await _run_stream_cmd(iterations=1)
    assert rc == 2


async def test_run_stream_cmd_returns_2_when_no_strategies_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRATEGIES_ENABLED", "")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "k")
    monkeypatch.setenv("PUBSUB_PROJECT_ID", "trade-ai-dev")
    rc = await _run_stream_cmd(iterations=1)
    assert rc == 2
