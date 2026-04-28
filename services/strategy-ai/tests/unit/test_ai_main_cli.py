from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from strategy_ai._testing import make_features


def _write_features(path: Path, count: int = 4) -> None:
    lines = [make_features(symbol="7203").model_dump_json() for _ in range(count)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_cli_backtest_with_default_fixture(tmp_path: Path) -> None:
    in_path = tmp_path / "features.jsonl"
    out_path = tmp_path / "signals.jsonl"
    _write_features(in_path, count=6)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "strategy_ai",
            "backtest",
            "--input",
            str(in_path),
            "--output",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line]
    # default fixture rotates BUY/HOLD/SELL → HOLD 抑止後 BUY,SELL が 2 周
    assert [r["action"] for r in rows] == ["BUY", "SELL", "BUY", "SELL"]
    assert all(r["source"] == "AI" for r in rows)


def test_cli_backtest_with_custom_fixture(tmp_path: Path) -> None:
    in_path = tmp_path / "features.jsonl"
    out_path = tmp_path / "signals.jsonl"
    fixture_path = tmp_path / "fixture.json"
    _write_features(in_path, count=2)
    fixture_path.write_text(
        json.dumps(
            [
                json.dumps({"action": "SELL", "confidence": 0.4, "reasoning": "x"}),
                json.dumps({"action": "BUY", "confidence": 0.4, "reasoning": "y"}),
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "strategy_ai",
            "backtest",
            "--input",
            str(in_path),
            "--output",
            str(out_path),
            "--fixture-responses",
            str(fixture_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line]
    assert [r["action"] for r in rows] == ["SELL", "BUY"]


def test_cli_backtest_missing_input(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "strategy_ai",
            "backtest",
            "--input",
            str(tmp_path / "missing.jsonl"),
            "--output",
            str(tmp_path / "out.jsonl"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2


def test_cli_stream_help_lists_iterations() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "strategy_ai", "stream", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--iterations" in result.stdout


def test_cli_stream_requires_supabase_env(tmp_path: Path) -> None:
    """環境変数が空のままだと SUPABASE_URL の警告で returncode=2 で抜ける。"""
    env = {
        "PATH": "/usr/bin:/bin",
        "BACKTEST_OUTPUT_DIR": str(tmp_path),
        "SUPABASE_URL": "",
        "SUPABASE_SECRET_KEY": "",
        "PUBSUB_PROJECT_ID": "",
        "GEMINI_API_KEY": "",
    }
    result = subprocess.run(
        [sys.executable, "-m", "strategy_ai", "stream", "--iterations", "1"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 2
    assert "SUPABASE_URL" in result.stderr or "SUPABASE_URL" in result.stdout
