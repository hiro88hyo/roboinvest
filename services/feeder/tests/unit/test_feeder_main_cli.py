"""``feeder.__main__`` の軽量 CLI テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest
from feeder.__main__ import build_parser, main


def test_parser_accepts_replay_stdout() -> None:
    parser = build_parser()

    args = parser.parse_args(["replay", "--input", "push.jsonl", "--sink", "stdout"])

    assert args.command == "replay"
    assert str(args.input_path) == "push.jsonl"
    assert args.sink == "stdout"


def test_parser_requires_subcommand() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_replay_missing_input_returns_config_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.jsonl"

    rc = main(["replay", "--input", str(missing)])

    assert rc == 2
